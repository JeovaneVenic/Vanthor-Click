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

from vanthor.capture.monitor import Frame
from vanthor.core.events import Event, EventBus, EventKind
from vanthor.rules.models import Rule
from vanthor.rules.runtime import RulePhase, RuleRuntime, RuleSignal
from vanthor.vision.color import ColorCondition, DetectionResult, detect

log = logging.getLogger(__name__)

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
            work = [(rule, self._runtimes[rule.id]) for rule in self._rules if rule.id in self._runtimes]
            zone_names = self._zone_names
        cache: dict[tuple[int, int, ColorCondition], DetectionResult] = {}
        for rule, runtime in work:
            assert rule.zone_id is not None  # garantido por Rule.runnable
            frame = self._frames(rule.zone_id)
            if frame is not None and frame is not self._last_frame.get(rule.id):
                self._last_frame[rule.id] = frame
                key = (rule.zone_id, id(frame), rule.condition)
                result = cache.get(key)
                if result is None:
                    result = detect(frame.image, rule.condition)
                    cache[key] = result
                self._last_ratio[rule.id] = result.ratio
                signals = runtime.on_frame(now, result.detected)
            else:
                signals = runtime.poll(now)
            for signal in signals:
                self._handle(rule, signal, zone_names.get(rule.zone_id, "zona"), runtime)

    def _handle(self, rule: Rule, signal: RuleSignal, zone_name: str, runtime: RuleRuntime) -> None:
        if signal is RuleSignal.APPEARED:
            with self._lock:
                self._detections[rule.id] = self._detections.get(rule.id, 0) + 1
                self._last_detection[rule.id] = datetime.now(UTC)
            self._recorder(rule.id, detections=1)
            self._bus.publish(Event(EventKind.DETECTION, f"{rule.name}: cor detectada em {zone_name}", rule_id=rule.id))
        elif signal is RuleSignal.FIRE:
            # A execução conta na decisão; os cliques são contados pela fila quando acontecem.
            self._recorder(rule.id, executions=1)
            self._executor.dispatch(rule)
        elif signal is RuleSignal.DELAY_CANCELLED:
            self._bus.publish(
                Event(EventKind.NOTICE, f"{rule.name}: a cor sumiu antes da hora de agir", rule_id=rule.id)
            )
        elif signal is RuleSignal.LIMIT_REACHED:
            self._bus.publish(
                Event(
                    EventKind.NOTICE,
                    f"{rule.name}: limite de {runtime.executions} execuções atingido",
                    rule_id=rule.id,
                )
            )
