"""Motor de regras: roda a cada ciclo do motor, depois da captura das zonas.

Para cada regra ativa: pega o quadro mais novo da zona dela, analisa a cor (uma vez por
zona+condição, mesmo que várias regras compartilhem), avança o avaliador da regra e executa a
ação quando for a hora. As regras são independentes entre si.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from vanthor.capture.monitor import Frame
from vanthor.core.events import Event, EventBus, EventKind, EventLevel
from vanthor.rules.models import Rule
from vanthor.rules.runtime import RulePhase, RuleRuntime, RuleSignal
from vanthor.vision.color import (
    ColorCondition,
    ColorSpace,
    DetectionResult,
    MatchMode,
    closest_color,
    detect,
)

log = logging.getLogger(__name__)

# Resumo periódico no log técnico, por regra. Uma linha por minuto mostra se a zona está sendo lida
# e qual a cor mais próxima do alvo, sem encher o arquivo (o motor roda 40 ciclos por segundo).
DIAGNOSTIC_INTERVAL_S = 60.0

FrameSource = Callable[[int], Frame | None]
Recorder = Callable[..., None]  # (rule_id, *, detections, executions, clicks)


class ActionExecutor(Protocol):
    """Recebe a ordem de agir. Não bloqueia: cliques vão para a fila de ações (``ActionDispatcher``)."""

    def dispatch(self, rule: Rule) -> None: ...


class LogOnlyExecutor:
    """Executor mínimo (testes e diagnóstico): só registra no histórico."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def dispatch(self, rule: Rule) -> None:
        self._bus.publish(Event(EventKind.ACTION, f"{rule.name}: registrado no histórico", rule_id=rule.id))


@dataclass(slots=True)
class _RuleDiagnostics:
    """Acumulado desde o último resumo (só a thread do motor mexe)."""

    since: float | None = None
    frames: int = 0
    max_matched: int = 0
    total_pixels: int = 0
    last_image: NDArray[np.uint8] | None = None
    reported_first: bool = False


def describe_condition(rule: Rule) -> str:
    """A configuração inteira da regra numa linha de log (para conferir o que o motor está usando)."""
    c = rule.condition
    if c.mode is MatchMode.PIXELS:
        need = f"pelo menos {c.min_pixels} pixel(s)"
    else:
        need = f"pelo menos {c.min_percent:g}% da zona"
    space = "RGB" if c.space is ColorSpace.RGB else "HSV"
    t = rule.trigger
    return (
        f"cor {c.hex}, {space} tolerância {c.tolerance}, {need}; confirma em {t.confirm_frames} quadro(s), "
        f"some após {t.release_frames} quadro(s), modo {t.mode.value}, espera {t.delay_ms} ms, "
        f"intervalo mínimo {t.cooldown_ms} ms"
    )


@dataclass(frozen=True, slots=True)
class RuleLive:
    phase: RulePhase
    executions: int  # nesta execução da automação
    last_ratio: float | None  # fração da zona com a cor no último quadro analisado
    detections: int = 0  # nesta execução
    last_detection: datetime | None = None


class RuleEngine:
    def __init__(
        self,
        bus: EventBus,
        frames: FrameSource,
        executor: ActionExecutor,
        recorder: Recorder,
    ) -> None:
        self._bus = bus
        self._frames = frames
        self._executor = executor
        self._recorder = recorder
        self._lock = threading.Lock()
        self._rules: list[Rule] = []
        self._zone_names: dict[int, str] = {}
        self._runtimes: dict[int, RuleRuntime] = {}
        self._keys: dict[int, tuple[object, ...]] = {}
        self._last_frame: dict[int, Frame] = {}
        self._last_ratio: dict[int, float] = {}
        self._detections: dict[int, int] = {}
        self._last_detection: dict[int, datetime] = {}
        self._diagnostics: dict[int, _RuleDiagnostics] = {}
        self._last_matched: dict[int, tuple[int, int]] = {}
        self._recorder_failing = False

    # -- configuração (qualquer thread) ----------------------------------------------
    def set_rules(self, rules: list[Rule], zone_names: dict[int, str]) -> None:
        """Troca as regras vigiadas. Regras sem mudança de comportamento mantêm o estado
        (não disparam de novo só porque o usuário renomeou outra regra)."""
        runnable = [r for r in rules if r.runnable]
        with self._lock:
            runtimes: dict[int, RuleRuntime] = {}
            keys: dict[int, tuple[object, ...]] = {}
            for rule in runnable:
                key = rule.behaviour_key()
                if self._keys.get(rule.id) == key and rule.id in self._runtimes:
                    runtimes[rule.id] = self._runtimes[rule.id]
                else:
                    runtimes[rule.id] = RuleRuntime(rule.trigger)
                    self._last_frame.pop(rule.id, None)
                    self._diagnostics.pop(rule.id, None)
                    zone = zone_names.get(rule.zone_id, str(rule.zone_id)) if rule.zone_id is not None else "-"
                    log.info("Automação %s (id %s) na zona %s: %s", rule.name, rule.id, zone, describe_condition(rule))
                keys[rule.id] = key
            self._rules = runnable
            self._zone_names = dict(zone_names)
            self._runtimes = runtimes
            self._keys = keys

    def reset(self) -> None:
        """Rotina de limpeza ao parar: a próxima execução recomeça do zero (ex.: disparo único)."""
        with self._lock:
            self._runtimes = {r.id: RuleRuntime(r.trigger) for r in self._rules}
            self._last_frame.clear()
            self._last_ratio.clear()
            self._detections.clear()
            self._last_detection.clear()
            self._diagnostics.clear()
            self._last_matched.clear()

    def live(self) -> dict[int, RuleLive]:
        with self._lock:
            return {
                rid: RuleLive(
                    rt.phase,
                    rt.executions,
                    self._last_ratio.get(rid),
                    self._detections.get(rid, 0),
                    self._last_detection.get(rid),
                )
                for rid, rt in self._runtimes.items()
            }

    # -- ciclo do motor -------------------------------------------------------------------
    def __call__(self, now: float) -> None:
        with self._lock:
            work = [
                (rule, self._runtimes[rule.id], self._diagnostics.setdefault(rule.id, _RuleDiagnostics()))
                for rule in self._rules
                if rule.id in self._runtimes
            ]
            zone_names = self._zone_names
        cache: dict[tuple[int, int, ColorCondition], DetectionResult] = {}
        for rule, runtime, diag in work:
            assert rule.zone_id is not None  # garantido por Rule.runnable
            zone_name = zone_names.get(rule.zone_id, "zona")
            if diag.since is None:
                diag.since = now
            frame = self._frames(rule.zone_id)
            if frame is not None and frame is not self._last_frame.get(rule.id):
                self._last_frame[rule.id] = frame
                key = (rule.zone_id, id(frame), rule.condition)
                result = cache.get(key)
                if result is None:
                    result = detect(frame.image, rule.condition)
                    cache[key] = result
                self._last_ratio[rule.id] = result.ratio
                self._last_matched[rule.id] = (result.matched_pixels, result.total_pixels)
                diag.frames += 1
                diag.max_matched = max(diag.max_matched, result.matched_pixels)
                diag.total_pixels = result.total_pixels
                diag.last_image = frame.image
                if log.isEnabledFor(logging.DEBUG):
                    log.debug(
                        "%s: %s de %s pixels com a cor, detectado=%s, fase=%s",
                        rule.name, result.matched_pixels, result.total_pixels, result.detected, runtime.phase.value,
                    )
                signals = runtime.on_frame(now, result.detected)
            else:
                signals = runtime.poll(now)
            for signal in signals:
                self._handle(rule, signal, zone_name, runtime)
            self._maybe_report(rule, zone_name, diag, runtime, now)

    def _maybe_report(
        self, rule: Rule, zone_name: str, diag: _RuleDiagnostics, runtime: RuleRuntime, now: float
    ) -> None:
        """Resumo no log técnico: no primeiro quadro analisado e depois uma vez por minuto."""
        assert diag.since is not None
        first = not diag.reported_first and diag.frames > 0
        if not first and now - diag.since < DIAGNOSTIC_INTERVAL_S:
            return
        if diag.frames == 0:
            # Um minuto inteiro sem quadro novo: a zona não está sendo capturada (veja os erros de captura).
            log.warning(
                "%s: nenhum quadro novo de %s em %.0f s (fase %s)",
                rule.name, zone_name, now - diag.since, runtime.phase.value,
            )
        else:
            closest = closest_color(diag.last_image, rule.condition.color) if diag.last_image is not None else None
            seen = (
                f"pixel mais parecido com {rule.condition.hex}: "
                f"#{closest.color[0]:02X}{closest.color[1]:02X}{closest.color[2]:02X} "
                f"(diferença {closest.distance}, tolerância {rule.condition.tolerance}) "
                f"em x+{closest.x} y+{closest.y}"
                if closest
                else "sem imagem"
            )
            log.info(
                "%s [%s]: %s quadro(s) em %.0f s; máximo de %s de %s pixels com a cor; %s; fase %s; "
                "%s execução(ões) nesta rodada",
                rule.name, zone_name, diag.frames, now - diag.since, diag.max_matched, diag.total_pixels,
                seen, runtime.phase.value, runtime.executions,
            )
        diag.reported_first = True
        diag.since = now
        diag.frames = 0
        diag.max_matched = 0

    def _handle(self, rule: Rule, signal: RuleSignal, zone_name: str, runtime: RuleRuntime) -> None:
        if signal is RuleSignal.APPEARED:
            with self._lock:
                self._detections[rule.id] = self._detections.get(rule.id, 0) + 1
                self._last_detection[rule.id] = datetime.now(UTC)
            matched, total = self._last_matched.get(rule.id, (0, 0))
            log.info(
                "%s: cor %s detectada em %s (%s de %s pixels)", rule.name, rule.condition.hex, zone_name, matched, total
            )
            self._record(rule, detections=1)
            self._bus.publish(Event(EventKind.DETECTION, f"{rule.name}: cor detectada em {zone_name}", rule_id=rule.id))
        elif signal is RuleSignal.FIRE:
            log.info("%s: hora de agir (execução %s nesta rodada)", rule.name, runtime.executions)
            # A execução conta na decisão; os cliques são contados pela fila quando acontecem.
            self._record(rule, executions=1)
            self._executor.dispatch(rule)
        elif signal is RuleSignal.DISAPPEARED:
            log.info("%s: a cor sumiu de %s; pronta para a próxima aparição", rule.name, zone_name)
        elif signal is RuleSignal.DELAY_CANCELLED:
            log.info("%s: a cor sumiu antes da hora de agir", rule.name)
            self._bus.publish(
                Event(EventKind.NOTICE, f"{rule.name}: a cor sumiu antes da hora de agir", rule_id=rule.id)
            )
        elif signal is RuleSignal.LIMIT_REACHED:
            log.info("%s: limite de %s execuções atingido", rule.name, runtime.executions)
            self._bus.publish(
                Event(
                    EventKind.NOTICE,
                    f"{rule.name}: limite de {runtime.executions} execuções atingido",
                    rule_id=rule.id,
                )
            )

    def _record(self, rule: Rule, **counts: int) -> None:
        """Grava os contadores sem deixar o banco derrubar a vigilância.

        Antes, um erro de gravação (disco cheio, banco travado) subia até o motor, que parava TODAS as
        automações. Contador é estatística; clicar na hora certa é o trabalho. Registramos e seguimos.
        """
        try:
            self._recorder(rule.id, **counts)
        except Exception as exc:
            if not self._recorder_failing:
                log.exception("Falha ao gravar contadores de %s; a automação continua", rule.name)
                self._bus.publish(
                    Event(
                        EventKind.ERROR,
                        f"Não foi possível gravar os contadores ({exc}). As automações continuam vigiando.",
                        EventLevel.WARNING,
                        rule_id=rule.id,
                    )
                )
            self._recorder_failing = True
            return
        if self._recorder_failing:
            log.info("Gravação de contadores normalizada")
        self._recorder_failing = False
