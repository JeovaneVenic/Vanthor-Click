"""Ações: modelo, controlador de entrada (com backend falso) e fila de execução."""

from __future__ import annotations

import threading
import time

import pytest

from vanthor.actions.controller import InputController
from vanthor.actions.dispatcher import ActionDispatcher
from vanthor.actions.models import ActionKind, ActionSpec, ClickStep, KeyStep, MouseButton, WaitStep
from vanthor.capture.geometry import ScreenRect
from vanthor.core.events import EventBus, EventKind
from vanthor.rules.models import Rule, RuleStatus
from vanthor.vision.color import ColorCondition
from vanthor.winsys.keys import KeyError_, parse_combo

from .conftest import Recorder

SCREEN = ScreenRect(0, 0, 1920, 1080)


# -- teclas ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "mods", "vk", "label"),
    [
        ("Enter", (), 0x0D, "Enter"),
        ("a", (), ord("A"), "A"),
        ("F5", (), 0x74, "F5"),
        ("Ctrl+Shift+S", (0x11, 0x10), ord("S"), "Ctrl+Shift+S"),
        ("alt+tab", (0x12,), 0x09, "Alt+Tab"),
        ("Down", (), 0x28, "Baixo"),
    ],
)
def test_parse_combo(text: str, mods: tuple[int, ...], vk: int, label: str) -> None:
    combo = parse_combo(text)
    assert (combo.modifiers, combo.vk, combo.label) == (mods, vk, label)


@pytest.mark.parametrize("text", ["", "Ctrl+", "Ctrl+Ctrl+A", "Hyper+A", "F99"])
def test_parse_combo_rejects(text: str) -> None:
    with pytest.raises(KeyError_):
        parse_combo(text)


# -- modelo ---------------------------------------------------------------------------------------------
def test_action_spec_roundtrip() -> None:
    spec = ActionSpec(
        ActionKind.SEQUENCE,
        (ClickStep(10, 20, MouseButton.RIGHT, True), WaitStep(250), KeyStep("ctrl+s")),
        restore_cursor=False,
    )
    assert ActionSpec.from_dict(spec.to_dict()) == spec
    assert spec.steps[2] == KeyStep("Ctrl+S")  # forma canônica
    assert spec.clicks == 2


def test_old_log_config_still_loads() -> None:
    assert ActionSpec.from_dict({"kind": "log"}) == ActionSpec()


@pytest.mark.parametrize(
    ("kind", "steps"),
    [
        (ActionKind.LOG, (ClickStep(1, 1),)),
        (ActionKind.CLICK, ()),
        (ActionKind.CLICK, (KeyStep("A"),)),
        (ActionKind.KEYS, (ClickStep(1, 1),)),
        (ActionKind.SEQUENCE, (WaitStep(100),)),  # só espera: não faz nada
        (ActionKind.SEQUENCE, tuple(ClickStep(i, i) for i in range(21))),
        (ActionKind.SEQUENCE, (ClickStep(1, 1), WaitStep(60_000), WaitStep(60_000), WaitStep(1))),
    ],
)
def test_action_spec_validation(kind: ActionKind, steps: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        ActionSpec(kind, steps)  # type: ignore[arg-type]


@pytest.mark.parametrize("data", [{"kind": "click", "steps": [{"type": "teleport"}]}, {"kind": "click", "steps": "x"}])
def test_action_from_dict_rejects_garbage(data: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ActionSpec.from_dict(data)


# -- controlador ---------------------------------------------------------------------------------------
class FakeBackend:
    def __init__(self, fail_on: str | None = None) -> None:
        self.events: list[tuple[object, ...]] = []
        self.pos = (5, 5)
        self.fail_on = fail_on

    def get_cursor(self) -> tuple[int, int]:
        return self.pos

    def set_cursor(self, x: int, y: int) -> None:
        self.pos = (x, y)
        self.events.append(("move", x, y))

    def mouse(self, button: MouseButton, down: bool) -> None:
        if self.fail_on == f"mouse-{'down' if down else 'up'}":
            raise RuntimeError("falha simulada")
        self.events.append((button.value, "down" if down else "up"))

    def key(self, vk: int, down: bool) -> None:
        if self.fail_on == f"key-{vk}":
            raise RuntimeError("falha simulada")
        self.events.append((vk, "down" if down else "up"))


def no_sleep(_s: float) -> None:
    return None


def test_click_moves_then_presses_and_releases() -> None:
    backend = FakeBackend()
    InputController(backend, no_sleep).click(ClickStep(100, 200, MouseButton.RIGHT))
    assert backend.events == [("move", 100, 200), ("right", "down"), ("right", "up")]


def test_double_click_sends_two_full_clicks() -> None:
    backend = FakeBackend()
    InputController(backend, no_sleep).click(ClickStep(1, 2, double=True))
    assert backend.events[1:] == [("left", "down"), ("left", "up")] * 2


def test_combo_presses_modifiers_first_and_releases_in_reverse() -> None:
    backend = FakeBackend()
    InputController(backend, no_sleep).press(parse_combo("Ctrl+Shift+S"))
    assert backend.events == [
        (0x11, "down"), (0x10, "down"), (ord("S"), "down"), (ord("S"), "up"), (0x10, "up"), (0x11, "up"),
    ]


def test_failure_mid_combo_leaves_nothing_held_after_release_all() -> None:
    backend = FakeBackend(fail_on=f"key-{ord('S')}")
    controller = InputController(backend, no_sleep)
    with pytest.raises(RuntimeError):
        controller.press(parse_combo("Ctrl+Shift+S"))
    assert controller.anything_held
    controller.release_all()
    assert not controller.anything_held
    assert backend.events[-2:] == [(0x10, "up"), (0x11, "up")]  # solta Shift e Ctrl, na ordem inversa


def test_failure_between_button_down_and_up_is_released() -> None:
    backend = FakeBackend(fail_on="mouse-up")
    controller = InputController(backend, no_sleep)
    with pytest.raises(RuntimeError):
        controller.click(ClickStep(1, 1))
    assert controller.anything_held
    backend.fail_on = None
    controller.release_all()
    assert backend.events[-1] == ("left", "up")


# -- fila de ações -----------------------------------------------------------------------------------------
def make_rule(rid: int, action: ActionSpec) -> Rule:
    return Rule(rid, 1, f"Regra {rid}", RuleStatus.ACTIVE, 1, ColorCondition((0, 0, 0)), action=action)


class Clicks:
    def __init__(self) -> None:
        self.total: dict[int, int] = {}

    def __call__(self, rule_id: int, **kw: int) -> None:
        self.total[rule_id] = self.total.get(rule_id, 0) + kw.get("clicks", 0)


@pytest.fixture
def queue(bus: EventBus) -> tuple[ActionDispatcher, FakeBackend, Clicks]:
    backend, clicks = FakeBackend(), Clicks()
    dispatcher = ActionDispatcher(bus, lambda: InputController(backend, no_sleep), clicks, lambda: [SCREEN])
    dispatcher.arm()
    yield dispatcher, backend, clicks
    dispatcher.halt()
    dispatcher.join()


def wait_for(predicate, timeout: float = 2.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def click_action(x: int, y: int, **kw: object) -> ActionSpec:
    return ActionSpec(ActionKind.CLICK, (ClickStep(x, y),), **kw)  # type: ignore[arg-type]


def test_click_runs_and_restores_cursor(queue: tuple[ActionDispatcher, FakeBackend, Clicks], recorder: Recorder) -> None:
    dispatcher, backend, clicks = queue
    dispatcher.dispatch(make_rule(1, click_action(300, 400)))
    assert wait_for(lambda: "action" in recorder.kinds)
    assert backend.events == [("move", 300, 400), ("left", "down"), ("left", "up"), ("move", 5, 5)]
    assert clicks.total == {1: 1}
    assert recorder.events[-1].message == "Regra 1: clique esquerdo em 300, 400"


def test_cursor_stays_when_restore_disabled(queue: tuple[ActionDispatcher, FakeBackend, Clicks], recorder: Recorder) -> None:
    dispatcher, backend, _ = queue
    dispatcher.dispatch(make_rule(1, click_action(300, 400, restore_cursor=False)))
    assert wait_for(lambda: "action" in recorder.kinds)
    assert backend.pos == (300, 400)


def test_actions_run_one_at_a_time_in_order(queue: tuple[ActionDispatcher, FakeBackend, Clicks], recorder: Recorder) -> None:
    dispatcher, backend, _ = queue
    for rid, x in ((1, 10), (2, 20), (3, 30)):
        dispatcher.dispatch(make_rule(rid, click_action(x, x, restore_cursor=False)))
    assert wait_for(lambda: recorder.kinds.count("action") == 3)
    moves = [e[1] for e in backend.events if e[0] == "move"]
    assert moves == [10, 20, 30]


def test_same_rule_is_not_queued_twice(bus: EventBus, recorder: Recorder) -> None:
    gate = threading.Event()
    backend = FakeBackend()

    def slow_sleep(_s: float) -> None:
        gate.wait(2)

    dispatcher = ActionDispatcher(bus, lambda: InputController(backend, slow_sleep), Clicks(), lambda: [SCREEN])
    dispatcher.arm()
    rule = make_rule(1, click_action(1, 1))
    dispatcher.dispatch(rule)  # começa a executar (preso no sleep)
    assert wait_for(lambda: dispatcher.pending_count == 0)
    for _ in range(5):
        dispatcher.dispatch(rule)  # só 1 fica na fila
    assert dispatcher.pending_count == 1
    gate.set()
    assert wait_for(lambda: recorder.kinds.count("action") == 2)
    dispatcher.halt()
    dispatcher.join()


def test_halt_interrupts_sequence_and_clears_queue(bus: EventBus, recorder: Recorder) -> None:
    backend = FakeBackend()
    dispatcher = ActionDispatcher(bus, lambda: InputController(backend, no_sleep), Clicks(), lambda: [SCREEN])
    dispatcher.arm()
    long = ActionSpec(ActionKind.SEQUENCE, (ClickStep(1, 1), WaitStep(10_000), ClickStep(2, 2)))
    dispatcher.dispatch(make_rule(1, long))
    dispatcher.dispatch(make_rule(2, click_action(9, 9)))
    assert wait_for(lambda: ("left", "up") in backend.events)
    started = time.monotonic()
    dispatcher.halt()
    dispatcher.join()
    assert time.monotonic() - started < 1  # não esperou os 10 s
    assert ("move", 2, 2) not in backend.events and ("move", 9, 9) not in backend.events
    assert recorder.events[-1].kind is EventKind.NOTICE
    dispatcher.dispatch(make_rule(3, click_action(5, 5)))  # parado: ignora
    assert dispatcher.pending_count == 0


def test_offscreen_point_is_refused(queue: tuple[ActionDispatcher, FakeBackend, Clicks], recorder: Recorder) -> None:
    dispatcher, backend, clicks = queue
    dispatcher.dispatch(make_rule(1, click_action(5000, 10)))
    assert wait_for(lambda: "error" in recorder.kinds)
    assert backend.events == [] and clicks.total == {}
    assert "fora das telas" in recorder.events[-1].message


def test_input_error_is_reported_and_releases(bus: EventBus, recorder: Recorder) -> None:
    backend = FakeBackend(fail_on="mouse-up")
    dispatcher = ActionDispatcher(bus, lambda: InputController(backend, no_sleep), Clicks(), lambda: [SCREEN])
    dispatcher.arm()
    dispatcher.dispatch(make_rule(1, click_action(10, 10)))
    assert wait_for(lambda: "error" in recorder.kinds)
    backend.fail_on = None
    dispatcher.halt()
    dispatcher.join()


def test_log_action_is_immediate_and_needs_no_input(bus: EventBus, recorder: Recorder) -> None:
    dispatcher = ActionDispatcher(bus, lambda: pytest.fail("não deveria criar controlador"), Clicks(), lambda: [SCREEN])
    dispatcher.dispatch(make_rule(1, ActionSpec()))  # nem precisa estar armado
    assert recorder.kinds == ["action"]


def test_sequence_with_keys_and_counts(queue: tuple[ActionDispatcher, FakeBackend, Clicks], recorder: Recorder) -> None:
    dispatcher, backend, clicks = queue
    seq = ActionSpec(ActionKind.SEQUENCE, (ClickStep(1, 1, double=True), WaitStep(10), KeyStep("Enter")))
    dispatcher.dispatch(make_rule(7, seq))
    assert wait_for(lambda: "action" in recorder.kinds)
    assert (0x0D, "down") in backend.events
    assert clicks.total == {7: 2}
    assert recorder.events[-1].message == "Regra 7: executar uma sequência de 3 passos"


def test_failed_cursor_restore_does_not_turn_click_into_error(bus: EventBus, recorder: Recorder) -> None:
    from vanthor.winsys.input import InputError

    class StubbornBackend(FakeBackend):
        def set_cursor(self, x: int, y: int) -> None:
            if (x, y) == (5, 5):  # devolver ao ponto original: o Windows recusa
                raise InputError("recusado")
            super().set_cursor(x, y)

    clicks = Clicks()
    backend = StubbornBackend()
    dispatcher = ActionDispatcher(bus, lambda: InputController(backend, no_sleep), clicks, lambda: [SCREEN])
    dispatcher.arm()
    dispatcher.dispatch(make_rule(1, click_action(300, 400)))
    assert wait_for(lambda: "action" in recorder.kinds)
    assert clicks.total == {1: 1} and "error" not in recorder.kinds
    dispatcher.halt()
    dispatcher.join()
