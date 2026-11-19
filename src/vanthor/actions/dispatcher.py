"""Fila de ações: executa cliques e teclas em ordem, numa thread própria.

Por que uma fila: o Windows tem um único cursor. Duas regras que disparam juntas não podem
"brigar" pelo mouse, então as ações saem uma de cada vez, com milissegundos de diferença.
Por que uma thread própria: uma sequência com esperas não pode travar o motor, que precisa
continuar vigiando as outras zonas.

Garantias:
- No máximo UMA ação pendente por regra (disparos repetidos não acumulam cliques atrasados).
- ``halt`` (parada/emergência) esvazia a fila, interrompe a sequência em andamento entre passos
  e solta qualquer botão ou tecla que tenha ficado pressionado.
- Modo de teste (dry run): percorre os passos e informa onde clicaria, sem NUNCA criar o
  controlador de entrada: não há caminho de código que mova o mouse nesse modo.
- Limite de cliques por minuto para todas as regras juntas (inclui o modo de teste).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from vanthor.actions.controller import InputController
from vanthor.actions.models import ActionKind, ClickStep, KeyStep, WaitStep
from vanthor.actions.rate_limit import ClickRateLimiter
from vanthor.capture.geometry import ScreenRect
from vanthor.core.events import Event, EventBus, EventKind, EventLevel
from vanthor.rules.models import Rule
from vanthor.winsys.input import InputError
from vanthor.winsys.keys import parse_combo

log = logging.getLogger(__name__)

Recorder = Callable[..., None]
ScreensProvider = Callable[[], list[ScreenRect]]
ControllerFactory = Callable[[], InputController]
SimulatedClick = Callable[[int, int, str], None]  # x, y, rótulo

_LIMIT_NOTICE_INTERVAL_S = 10.0


@dataclass(frozen=True, slots=True)
class _Job:
    rule: Rule


class ActionDispatcher:
    def __init__(
        self,
        bus: EventBus,
        controller_factory: ControllerFactory,
        recorder: Recorder,
        screens: ScreensProvider,
        on_simulated_click: SimulatedClick | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bus = bus
        self._on_simulated_click = on_simulated_click
        self._clock = clock
        self._dry_run = False
        self._limiter = ClickRateLimiter(120, clock)
        self._last_limit_notice = -_LIMIT_NOTICE_INTERVAL_S
        self._factory = controller_factory
        self._recorder = recorder
        self._screens = screens
        self._controller: InputController | None = None
        self._lock = threading.Condition()
        self._queue: deque[_Job] = deque()
        self._pending: set[int] = set()
        self._halt = threading.Event()
        self._halt.set()  # começa desarmado: nada executa antes de a automação iniciar
        self._thread: threading.Thread | None = None

    # -- ciclo de vida (chamado pela sessão e pelos hooks do motor) ----------------------------
    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def set_recorder(self, recorder: Recorder) -> None:
        self._recorder = recorder

    def set_simulated_click_listener(self, listener: SimulatedClick | None) -> None:
        self._on_simulated_click = listener

    def arm(self, dry_run: bool = False, max_clicks_per_minute: int = 120) -> None:
        """Prepara para executar ações. Chamado ao iniciar a automação."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._dry_run = dry_run
            self._limiter = ClickRateLimiter(max_clicks_per_minute, self._clock)
            self._halt.clear()
            self._thread = threading.Thread(target=self._work, name="vanthor-actions", daemon=True)
            self._thread.start()

    def halt(self) -> None:
        """Safety hook: interrompe tudo AGORA e solta entradas. Qualquer thread."""
        self._halt.set()
        with self._lock:
            self._queue.clear()
            self._pending.clear()
            self._lock.notify_all()
        if self._controller is not None:
            self._controller.release_all()

    def join(self, timeout_s: float = 2.0) -> None:
        """Cleanup hook: espera a thread de ações terminar."""
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_s)
            if thread.is_alive():
                log.error("A thread de ações não encerrou em %.1fs", timeout_s)
        self._thread = None

    def clear_pending(self) -> None:
        """Ao pausar: descarta o que ainda não começou (não age "atrasado" depois de retomar)."""
        with self._lock:
            self._queue.clear()
            self._pending.clear()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    # -- entrada (thread do motor) ----------------------------------------------------------
    def dispatch(self, rule: Rule) -> None:
        if rule.action.kind is ActionKind.LOG:
            # Só registrar não usa mouse nem teclado: não precisa esperar na fila.
            kind = EventKind.SIMULATED_ACTION if self._dry_run else EventKind.ACTION
            self._bus.publish(Event(kind, f"{rule.name}: registrado no histórico", rule_id=rule.id))
            return
        with self._lock:
            if self._halt.is_set():
                return
            if rule.id in self._pending:
                log.debug("Ação de %s já está na fila; disparo ignorado", rule.name)
                return
            self._pending.add(rule.id)
            self._queue.append(_Job(rule))
            self._lock.notify()

    # -- execução (thread de ações) ----------------------------------------------------------
    def _work(self) -> None:
        while True:
            with self._lock:
                while not self._queue and not self._halt.is_set():
                    self._lock.wait()
                if self._halt.is_set():
                    return
                job = self._queue.popleft()
                self._pending.discard(job.rule.id)
            try:
                if self._dry_run:
                    self._simulate(job.rule)
                else:
                    if self._controller is None:
                        self._controller = self._factory()
                    self._execute(job.rule, self._controller)
            except Exception as exc:
                log.exception("Falha ao executar a ação de %s", job.rule.name)
                if self._controller is not None:
                    self._controller.release_all()
                self._bus.publish(
                    Event(EventKind.ERROR, f"{job.rule.name}: {exc}", EventLevel.WARNING, rule_id=job.rule.id)
                )

    def _preflight(self, rule: Rule) -> bool:
        """Validações antes de agir (valem também no modo de teste, para mostrar o que aconteceria)."""
        action = rule.action
        screens = self._screens()
        offscreen = [
            s for s in action.steps if isinstance(s, ClickStep) and not any(r.contains_point(s.x, s.y) for r in screens)
        ]
        if offscreen:
            self._bus.publish(
                Event(
                    EventKind.ERROR,
                    f"{rule.name}: o ponto {offscreen[0].x}, {offscreen[0].y} está fora das telas. Nada foi clicado.",
                    EventLevel.WARNING,
                    rule_id=rule.id,
                )
            )
            return False
        if not self._limiter.try_take(action.clicks):
            now = self._clock()
            if now - self._last_limit_notice >= _LIMIT_NOTICE_INTERVAL_S:
                self._last_limit_notice = now
                self._bus.publish(
                    Event(
                        EventKind.ERROR,
                        f"Limite de {self._limiter.limit} cliques por minuto atingido. "
                        f"{rule.name} e outras ações ficam de fora até o minuto liberar.",
                        EventLevel.WARNING,
                        rule_id=rule.id,
                    )
                )
            return False
        return True

    def _simulate(self, rule: Rule) -> None:
        if not self._preflight(rule):
            return
        interrupted = False
        for step in rule.action.steps:
            if self._halt.is_set():
                interrupted = True
                break
            if isinstance(step, ClickStep):
                if self._on_simulated_click is not None:
                    self._on_simulated_click(step.x, step.y, f"{rule.name}: clique simulado")
            elif isinstance(step, WaitStep) and self._halt.wait(step.ms / 1000):
                interrupted = True
                break
        message = (
            f"{rule.name}: teste interrompido pela parada"
            if interrupted
            else f"{rule.name}: teria executado: {rule.action.describe()}"
        )
        self._bus.publish(Event(EventKind.SIMULATED_ACTION, message, rule_id=rule.id))

    def _execute(self, rule: Rule, controller: InputController) -> None:
        action = rule.action
        if not self._preflight(rule):
            return

        original = controller.cursor() if action.moves_mouse and action.restore_cursor else None
        clicks = 0
        interrupted = False
        try:
            for step in action.steps:
                if self._halt.is_set():
                    interrupted = True
                    break
                if isinstance(step, ClickStep):
                    controller.click(step)
                    clicks += 2 if step.double else 1
                elif isinstance(step, KeyStep):
                    controller.press(parse_combo(step.combo))
                elif isinstance(step, WaitStep) and self._halt.wait(step.ms / 1000):
                    interrupted = True
                    break
        finally:
            if original is not None:
                try:
                    controller.move(*original)
                except InputError:
                    # Devolver o mouse é cortesia: se o Windows recusar (tela bloqueada, protetor
                    # de tela), o clique já aconteceu e não vira erro da automação.
                    log.warning("Não foi possível devolver o mouse para %s", original)
            if clicks:
                self._recorder(rule.id, clicks=clicks)

        if interrupted:
            self._bus.publish(
                Event(EventKind.NOTICE, f"{rule.name}: sequência interrompida pela parada", rule_id=rule.id)
            )
        else:
            self._bus.publish(Event(EventKind.ACTION, f"{rule.name}: {action.describe()}", rule_id=rule.id))
