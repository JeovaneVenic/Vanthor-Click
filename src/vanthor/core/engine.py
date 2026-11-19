"""Motor de automação: ciclo de vida, laço de verificação e garantias de segurança.

Garantias:
- Só inicia com chamada explícita a ``start()`` (botão do usuário). Nada inicia sozinho.
- ``stop()`` é idempotente, pode ser chamado de qualquer thread (inclusive a emergência)
  e sempre roda os *safety hooks*, que liberam botões e teclas pressionados.
- Um erro dentro de um ciclo para o motor em vez de deixá-lo rodando em estado incerto.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from vanthor.core.events import Event, EventBus, EventKind, EventLevel

log = logging.getLogger(__name__)

Clock = Callable[[], float]
TickHandler = Callable[[float], None]
SafetyHook = Callable[[], None]


class EngineState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class StopReason(StrEnum):
    USER = "user"
    EMERGENCY = "emergency"
    SHUTDOWN = "shutdown"
    ERROR = "error"


_STOP_MESSAGES: dict[StopReason, str] = {
    StopReason.USER: "Automações paradas",
    StopReason.EMERGENCY: "Parada de emergência acionada",
    StopReason.SHUTDOWN: "Automações paradas ao fechar o aplicativo",
    StopReason.ERROR: "Automações paradas por erro",
}


@dataclass(frozen=True, slots=True)
class EngineSnapshot:
    state: EngineState
    active_seconds: float  # tempo vigiando, sem contar pausas
    ticks: int


class AutomationEngine:
    def __init__(
        self,
        bus: EventBus,
        tick_interval_s: float = 0.025,
        clock: Clock = time.monotonic,
    ) -> None:
        if tick_interval_s <= 0:
            raise ValueError("tick_interval_s precisa ser positivo")
        self._bus = bus
        self._interval = tick_interval_s
        self._clock = clock
        self._lock = threading.RLock()
        self._state = EngineState.STOPPED
        self._thread: threading.Thread | None = None
        self._stop_signal = threading.Event()
        self._accumulated = 0.0
        self._running_since: float | None = None
        self._ticks = 0
        self._tick_handlers: list[TickHandler] = []
        self._safety_hooks: list[SafetyHook] = []
        self._cleanup_hooks: list[SafetyHook] = []
        self._thread_exit_hooks: list[SafetyHook] = []

    # -- extensão -------------------------------------------------------------------
    def add_tick_handler(self, handler: TickHandler) -> None:
        """Registra quem trabalha a cada ciclo (o avaliador de regras, a partir da Fase 4)."""
        with self._lock:
            self._tick_handlers.append(handler)

    def add_safety_hook(self, hook: SafetyHook) -> None:
        """Registra uma rotina de liberação executada em TODA parada (ex.: soltar botões)."""
        with self._lock:
            self._safety_hooks.append(hook)

    def add_cleanup_hook(self, hook: SafetyHook) -> None:
        """Registra uma limpeza executada DEPOIS que a thread do motor terminou (ex.: fechar captura).

        Diferente dos safety hooks, que rodam antes para soltar o mouse o quanto antes, estes
        esperam o fim do ciclo em andamento para não liberar um recurso que ainda está em uso.
        """
        with self._lock:
            self._cleanup_hooks.append(hook)

    def add_thread_exit_hook(self, hook: SafetyHook) -> None:
        """Registra uma rotina executada DENTRO da thread do motor quando ela termina.

        Para recursos presos à thread que os criou (ex.: contextos gráficos do Windows usados na
        captura), que não podem ser liberados por outra thread.
        """
        with self._lock:
            self._thread_exit_hooks.append(hook)

    # -- ciclo de vida ----------------------------------------------------------------
    @property
    def state(self) -> EngineState:
        with self._lock:
            return self._state

    def start(self) -> bool:
        with self._lock:
            if self._state is not EngineState.STOPPED:
                return False
            self._stop_signal = threading.Event()
            self._accumulated = 0.0
            self._ticks = 0
            self._running_since = self._clock()
            self._state = EngineState.RUNNING
            self._thread = threading.Thread(
                target=self._run, args=(self._stop_signal,), name="vanthor-engine", daemon=True
            )
            self._thread.start()
        self._bus.publish(Event(EventKind.ENGINE_STARTED, "Automações iniciadas"))
        return True

    def pause(self) -> bool:
        with self._lock:
            if self._state is not EngineState.RUNNING:
                return False
            self._bank_running_time()
            self._state = EngineState.PAUSED
        self._bus.publish(Event(EventKind.ENGINE_PAUSED, "Automações pausadas"))
        return True

    def resume(self) -> bool:
        with self._lock:
            if self._state is not EngineState.PAUSED:
                return False
            self._running_since = self._clock()
            self._state = EngineState.RUNNING
        self._bus.publish(Event(EventKind.ENGINE_RESUMED, "Automações retomadas"))
        return True

    def stop(self, reason: StopReason = StopReason.USER, join_timeout_s: float = 2.0) -> bool:
        with self._lock:
            if self._state is EngineState.STOPPED:
                return False
            if self._state is EngineState.RUNNING:
                self._bank_running_time()
            self._state = EngineState.STOPPED
            self._stop_signal.set()
            thread = self._thread
            self._thread = None
            hooks = list(self._safety_hooks)
            cleanups = list(self._cleanup_hooks)

        # Libera entradas ANTES de esperar a thread: numa emergência, soltar o mouse é
        # mais urgente que encerrar o laço com elegância.
        self._run_safety_hooks(hooks)
        joined = True
        if thread is not None and thread is not threading.current_thread():
            thread.join(join_timeout_s)
            if thread.is_alive():
                joined = False
                log.error("A thread do motor não encerrou em %.1fs", join_timeout_s)
        if joined:
            self._run_safety_hooks(cleanups)
        else:
            log.error("Limpeza adiada: a thread do motor ainda está ativa")

        kind = EventKind.EMERGENCY_STOP if reason is StopReason.EMERGENCY else EventKind.ENGINE_STOPPED
        level = EventLevel.INFO if reason in (StopReason.USER, StopReason.SHUTDOWN) else EventLevel.WARNING
        self._bus.publish(Event(kind, _STOP_MESSAGES[reason], level))
        return True

    def emergency_stop(self) -> bool:
        return self.stop(StopReason.EMERGENCY)

    def snapshot(self) -> EngineSnapshot:
        with self._lock:
            active = self._accumulated
            if self._state is EngineState.RUNNING and self._running_since is not None:
                active += self._clock() - self._running_since
            return EngineSnapshot(self._state, active, self._ticks)

    # -- interno ----------------------------------------------------------------------
    def _bank_running_time(self) -> None:
        if self._running_since is not None:
            self._accumulated += self._clock() - self._running_since
            self._running_since = None

    def _run(self, stop_signal: threading.Event) -> None:
        try:
            self._loop(stop_signal)
        finally:
            with self._lock:
                exit_hooks = list(self._thread_exit_hooks)
            self._run_safety_hooks(exit_hooks)

    def _loop(self, stop_signal: threading.Event) -> None:
        # Ritmo por horário marcado: o tempo gasto no ciclo é descontado da espera. Dormir o
        # intervalo inteiro DEPOIS do trabalho faria o ciclo real ser intervalo + trabalho.
        # time.sleep usa o temporizador de alta resolução do Windows (Python 3.11+); Event.wait
        # arredonda para ~15,6 ms e transformaria 25 ms em 31 ms. A parada é conferida a cada ciclo.
        next_tick = time.monotonic() + self._interval
        while True:
            remaining = next_tick - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            if stop_signal.is_set():
                return
            next_tick += self._interval
            if next_tick < time.monotonic():
                # Sobrecarga (ciclo mais longo que o intervalo): segue sem tentar "recuperar" atraso.
                next_tick = time.monotonic() + self._interval
            with self._lock:
                if self._state is not EngineState.RUNNING:
                    continue
                self._ticks += 1
                handlers = list(self._tick_handlers)
            now = self._clock()
            for handler in handlers:
                if stop_signal.is_set():
                    return
                try:
                    handler(now)
                except Exception as exc:
                    log.exception("Erro durante o ciclo de automação")
                    self._bus.publish(
                        Event(EventKind.ERROR, f"Erro durante a automação: {exc}", EventLevel.ERROR)
                    )
                    self.stop(StopReason.ERROR)
                    return

    @staticmethod
    def _run_safety_hooks(hooks: list[SafetyHook]) -> None:
        for hook in hooks:
            try:
                hook()
            except Exception:
                # Cada hook roda mesmo que outro falhe: um botão preso é pior que um log.
                log.exception("Falha ao executar rotina de liberação")
