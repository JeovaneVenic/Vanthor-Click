"""Fase 6: limite de cliques por minuto, modo de teste e configurações de segurança."""

from __future__ import annotations

import time

import pytest

from vanthor.actions.controller import InputController
from vanthor.actions.dispatcher import ActionDispatcher
from vanthor.actions.models import ActionKind, ActionSpec, ClickStep, KeyStep, WaitStep
from vanthor.actions.rate_limit import ClickRateLimiter
from vanthor.capture.geometry import ScreenRect
from vanthor.core.events import EventBus, EventKind
from vanthor.persistence.database import Database
from vanthor.persistence.repositories import AppSettings, SettingsRepository

from .conftest import FakeClock, Recorder
from .test_actions import Clicks, FakeBackend, make_rule, no_sleep, wait_for

SCREEN = ScreenRect(0, 0, 1920, 1080)


# -- limite por minuto ------------------------------------------------------------------------------
def test_rate_limiter_sliding_window(clock: FakeClock) -> None:
    limiter = ClickRateLimiter(3, clock)
    assert limiter.try_take(1) and limiter.try_take(2)
    assert not limiter.try_take(1)
    clock.advance(59.9)
    assert not limiter.try_take(1)
    clock.advance(0.1)  # os 3 cliques de t=0 saem da janela
    assert limiter.try_take(3)
    assert limiter.used() == 3


def test_rate_limiter_refuses_whole_action() -> None:
    limiter = ClickRateLimiter(3)
    assert limiter.try_take(2)
    assert not limiter.try_take(2)  # não clica "pela metade"
    assert limiter.used() == 2


def test_rate_limiter_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError):
        ClickRateLimiter(0)


def click_rule(rid: int, double: bool = False) -> object:
    return make_rule(rid, ActionSpec(ActionKind.CLICK, (ClickStep(10, 10, double=double),)))


def test_dispatcher_enforces_clicks_per_minute(bus: EventBus, recorder: Recorder) -> None:
    backend, clicks = FakeBackend(), Clicks()
    dispatcher = ActionDispatcher(bus, lambda: InputController(backend, no_sleep), clicks, lambda: [SCREEN])
    dispatcher.arm(max_clicks_per_minute=3)
    for rid in (1, 2, 3, 4, 5):
        dispatcher.dispatch(click_rule(rid))  # type: ignore[arg-type]
        assert wait_for(lambda: dispatcher.pending_count == 0)
        time.sleep(0.02)
    assert wait_for(lambda: sum(clicks.total.values()) == 3)
    time.sleep(0.05)
    assert sum(clicks.total.values()) == 3
    warnings = [e for e in recorder.events if e.kind is EventKind.ERROR]
    assert len(warnings) == 1  # avisa uma vez, não a cada ação recusada
    assert "Limite de 3 cliques por minuto" in warnings[0].message
    dispatcher.halt()
    dispatcher.join()


def test_double_click_counts_as_two(bus: EventBus) -> None:
    backend, clicks = FakeBackend(), Clicks()
    dispatcher = ActionDispatcher(bus, lambda: InputController(backend, no_sleep), clicks, lambda: [SCREEN])
    dispatcher.arm(max_clicks_per_minute=3)
    dispatcher.dispatch(click_rule(1, double=True))  # type: ignore[arg-type]
    assert wait_for(lambda: clicks.total.get(1) == 2)
    dispatcher.dispatch(click_rule(2, double=True))  # type: ignore[arg-type]  # 2 + 2 > 3: recusa
    time.sleep(0.1)
    assert clicks.total.get(2) is None
    dispatcher.halt()
    dispatcher.join()


# -- modo de teste ---------------------------------------------------------------------------------------
def test_dry_run_never_touches_input_and_reports_points(bus: EventBus, recorder: Recorder) -> None:
    simulated: list[tuple[int, int, str]] = []
    clicks = Clicks()
    dispatcher = ActionDispatcher(
        bus,
        lambda: pytest.fail("o modo de teste não pode criar o controlador de entrada"),
        clicks,
        lambda: [SCREEN],
        on_simulated_click=lambda x, y, label: simulated.append((x, y, label)),
    )
    dispatcher.arm(dry_run=True)
    seq = ActionSpec(ActionKind.SEQUENCE, (ClickStep(100, 200), WaitStep(5), KeyStep("Enter"), ClickStep(300, 400)))
    dispatcher.dispatch(make_rule(1, seq))
    assert wait_for(lambda: "simulated_action" in recorder.kinds)
    assert [(x, y) for x, y, _ in simulated] == [(100, 200), (300, 400)]
    assert clicks.total == {}  # cliques simulados não contam como cliques reais
    assert recorder.events[-1].message == "Regra 1: teria executado: executar uma sequência de 4 passos"
    dispatcher.halt()
    dispatcher.join()


def test_dry_run_also_respects_click_limit(bus: EventBus, recorder: Recorder) -> None:
    simulated: list[tuple[int, int, str]] = []
    dispatcher = ActionDispatcher(
        bus, lambda: pytest.fail("sem entrada"), Clicks(), lambda: [SCREEN],
        on_simulated_click=lambda x, y, label: simulated.append((x, y, label)),
    )
    dispatcher.arm(dry_run=True, max_clicks_per_minute=1)
    dispatcher.dispatch(click_rule(1))  # type: ignore[arg-type]
    assert wait_for(lambda: len(simulated) == 1)
    dispatcher.dispatch(click_rule(2))  # type: ignore[arg-type]
    assert wait_for(lambda: "error" in recorder.kinds)
    assert len(simulated) == 1
    dispatcher.halt()
    dispatcher.join()


def test_log_action_in_dry_run_is_marked_simulated(bus: EventBus, recorder: Recorder) -> None:
    dispatcher = ActionDispatcher(bus, lambda: pytest.fail("sem entrada"), Clicks(), lambda: [SCREEN])
    dispatcher.arm(dry_run=True)
    dispatcher.dispatch(make_rule(1, ActionSpec()))
    assert recorder.kinds == ["simulated_action"]
    dispatcher.halt()
    dispatcher.join()


def test_rearm_switches_back_to_real_mode(bus: EventBus) -> None:
    backend, clicks = FakeBackend(), Clicks()
    dispatcher = ActionDispatcher(bus, lambda: InputController(backend, no_sleep), clicks, lambda: [SCREEN])
    dispatcher.arm(dry_run=True)
    dispatcher.halt()
    dispatcher.join()
    dispatcher.arm(dry_run=False)
    assert not dispatcher.dry_run
    dispatcher.dispatch(click_rule(1))  # type: ignore[arg-type]
    assert wait_for(lambda: clicks.total.get(1) == 1)
    dispatcher.halt()
    dispatcher.join()


# -- configurações -----------------------------------------------------------------------------------------
def test_new_settings_defaults_and_roundtrip(db: Database) -> None:
    repo = SettingsRepository(db)
    assert repo.load() == AppSettings()
    repo.save(AppSettings(max_clicks_per_minute=30, notifications=False, show_indicator=False))
    loaded = repo.load()
    assert (loaded.max_clicks_per_minute, loaded.notifications, loaded.show_indicator) == (30, False, False)


@pytest.mark.parametrize("value", [0, 1201, -5])
def test_click_limit_out_of_range_is_rejected(value: int) -> None:
    with pytest.raises(ValueError):
        AppSettings(max_clicks_per_minute=value)


@pytest.mark.parametrize("raw", ["0", "99999", "true"])
def test_bad_click_limit_in_database_falls_back(db: Database, raw: str) -> None:
    db.execute("INSERT INTO settings (key, value) VALUES ('max_clicks_per_minute', ?)", (raw,))
    assert SettingsRepository(db).load().max_clicks_per_minute == 120
