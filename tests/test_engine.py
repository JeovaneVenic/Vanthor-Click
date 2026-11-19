from __future__ import annotations

import threading
import time

import pytest

from vanthor.core.engine import AutomationEngine, EngineState, StopReason
from vanthor.core.events import EventBus, EventKind, EventLevel

from .conftest import FakeClock, Recorder


def wait_until(predicate, timeout: float = 2.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_engine_starts_stopped_and_never_runs_on_its_own(bus: EventBus) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    time.sleep(0.05)
    assert engine.state is EngineState.STOPPED
    assert engine.snapshot().ticks == 0


def test_start_runs_ticks_and_stop_halts_them(bus: EventBus, recorder: Recorder) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    calls: list[float] = []
    engine.add_tick_handler(calls.append)

    assert engine.start() is True
    assert wait_until(lambda: len(calls) >= 3)
    assert engine.stop() is True

    count_after_stop = len(calls)
    time.sleep(0.05)
    assert len(calls) == count_after_stop
    assert engine.state is EngineState.STOPPED
    assert recorder.kinds == ["engine_started", "engine_stopped"]


def test_start_twice_is_rejected(bus: EventBus) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    assert engine.start()
    assert engine.start() is False
    engine.stop()


def test_stop_is_idempotent(bus: EventBus, recorder: Recorder) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    assert engine.stop() is False
    engine.start()
    assert engine.stop() is True
    assert engine.stop() is False
    assert recorder.kinds.count("engine_stopped") == 1


def test_pause_stops_ticking_and_resume_continues(bus: EventBus) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    calls: list[float] = []
    engine.add_tick_handler(calls.append)
    engine.start()
    assert wait_until(lambda: len(calls) >= 2)

    assert engine.pause()
    time.sleep(0.02)  # deixa terminar um ciclo que já estivesse em andamento
    paused_count = len(calls)
    time.sleep(0.05)
    assert len(calls) == paused_count

    assert engine.resume()
    assert wait_until(lambda: len(calls) > paused_count)
    engine.stop()


def test_active_time_excludes_pauses(bus: EventBus, clock: FakeClock) -> None:
    engine = AutomationEngine(bus, tick_interval_s=10, clock=clock)
    engine.start()
    clock.advance(30)
    engine.pause()
    clock.advance(100)  # pausado: não conta
    assert engine.snapshot().active_seconds == pytest.approx(30)
    engine.resume()
    clock.advance(5)
    assert engine.snapshot().active_seconds == pytest.approx(35)
    engine.stop()
    clock.advance(50)  # parado: congela
    assert engine.snapshot().active_seconds == pytest.approx(35)


def test_restart_resets_active_time(bus: EventBus, clock: FakeClock) -> None:
    engine = AutomationEngine(bus, tick_interval_s=10, clock=clock)
    engine.start()
    clock.advance(20)
    engine.stop()
    engine.start()
    assert engine.snapshot().active_seconds == pytest.approx(0)
    engine.stop()


def test_every_stop_runs_safety_hooks_even_if_one_fails(bus: EventBus) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    released: list[str] = []

    def broken_hook() -> None:
        raise RuntimeError("falha simulada")

    engine.add_safety_hook(broken_hook)
    engine.add_safety_hook(lambda: released.append("mouse"))

    for reason in (StopReason.USER, StopReason.EMERGENCY, StopReason.SHUTDOWN):
        engine.start()
        engine.stop(reason)
    assert released == ["mouse", "mouse", "mouse"]


def test_emergency_stop_publishes_warning(bus: EventBus, recorder: Recorder) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    engine.start()
    engine.pause()
    assert engine.emergency_stop()
    last = recorder.events[-1]
    assert last.kind is EventKind.EMERGENCY_STOP
    assert last.level is EventLevel.WARNING
    assert engine.state is EngineState.STOPPED


def test_emergency_stop_from_another_thread(bus: EventBus) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    engine.start()
    worker = threading.Thread(target=engine.emergency_stop)
    worker.start()
    worker.join(2)
    assert engine.state is EngineState.STOPPED


def test_error_in_tick_stops_engine_and_releases_inputs(bus: EventBus, recorder: Recorder) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    released: list[bool] = []
    engine.add_safety_hook(lambda: released.append(True))

    def failing_tick(_now: float) -> None:
        raise ValueError("captura falhou")

    engine.add_tick_handler(failing_tick)
    engine.start()
    assert wait_until(lambda: engine.state is EngineState.STOPPED)
    assert released == [True]
    assert "error" in recorder.kinds
    assert recorder.events[-1].kind is EventKind.ENGINE_STOPPED
    assert recorder.events[-1].level is EventLevel.WARNING


def test_invalid_interval_is_rejected(bus: EventBus) -> None:
    with pytest.raises(ValueError):
        AutomationEngine(bus, tick_interval_s=0)


def test_thread_exit_hooks_run_inside_engine_thread(bus: EventBus) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    seen: list[str] = []
    engine.add_tick_handler(lambda _now: seen.append(threading.current_thread().name) if not seen else None)
    exits: list[str] = []
    engine.add_thread_exit_hook(lambda: exits.append(threading.current_thread().name))
    engine.start()
    assert wait_until(lambda: bool(seen))
    engine.stop()
    assert exits == ["vanthor-engine"] == seen


def test_cleanup_runs_after_thread_exit_hooks(bus: EventBus) -> None:
    engine = AutomationEngine(bus, tick_interval_s=0.005)
    order: list[str] = []
    engine.add_thread_exit_hook(lambda: order.append("saida-da-thread"))
    engine.add_cleanup_hook(lambda: order.append("limpeza"))
    engine.add_safety_hook(lambda: order.append("soltar-entradas"))
    engine.start()
    engine.stop()
    assert order == ["soltar-entradas", "saida-da-thread", "limpeza"]
