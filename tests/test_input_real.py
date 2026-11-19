"""Aceite da Fase 5: cliques e teclas REAIS chegam exatamente onde devem (com escala de DPI)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
from pytestqt.qtbot import QtBot  # noqa: E402

from vanthor.actions.controller import InputController  # noqa: E402
from vanthor.actions.models import ActionKind, ActionSpec, ClickStep, KeyStep, MouseButton, WaitStep  # noqa: E402

from vanthor.core.events import EventKind  # noqa: E402
from vanthor.persistence.database import Database  # noqa: E402
from vanthor.rules.models import RuleStatus, TriggerSettings  # noqa: E402
from vanthor.ui.screens import map_for_screen, monitor_maps  # noqa: E402
from vanthor.vision.color import ColorCondition  # noqa: E402
from vanthor.winsys.input import Win32Input  # noqa: E402
from vanthor.winsys.hotkeys import taken_by_another_program  # noqa: E402
from vanthor.winsys.keys import parse_combo  # noqa: E402

from .test_capture_dpi import physical_rect_of  # noqa: E402
from .test_rules_e2e import Lamp, rgb  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="entrada real do Windows")


class Target(QWidget):
    """Janela-alvo que anota cada clique (em pixels físicos) e cada tecla recebida."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.clicks: list[tuple[str, int, int]] = []
        self.doubles = 0
        self.keys: list[tuple[int, Qt.KeyboardModifier]] = []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _physical(self, pos: QPointF) -> tuple[int, int]:
        monitor = map_for_screen(self.screen(), monitor_maps())
        return monitor.to_physical(pos.x(), pos.y())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        name = {Qt.MouseButton.LeftButton: "left", Qt.MouseButton.RightButton: "right",
                Qt.MouseButton.MiddleButton: "middle"}[event.button()]
        self.clicks.append((name, *self._physical(event.globalPosition())))

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.doubles += 1

    def keyPressEvent(self, event: QKeyEvent) -> None:
        self.keys.append((event.key(), event.modifiers()))

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#334155"))
        p.end()


@pytest.fixture
def target(qtbot: QtBot) -> Target:
    window = Target()
    qtbot.addWidget(window)
    geo = QGuiApplication.primaryScreen().geometry()
    window.setGeometry(geo.x() + 600, geo.y() + 320, 220, 120)
    window.show()
    qtbot.waitExposed(window)
    qtbot.wait(100)
    return window


@pytest.fixture
def controller() -> Iterator[InputController]:
    ctl = InputController(Win32Input())
    original = ctl.cursor()
    yield ctl
    ctl.release_all()
    ctl.move(*original)  # devolve o mouse de quem está rodando os testes


@pytest.mark.parametrize(("dx", "dy"), [(3, 3), (57, 41), (200, 100)])
def test_click_lands_on_exact_physical_pixel(qtbot: QtBot, target: Target, controller: InputController,
                                             dx: int, dy: int) -> None:
    rect = physical_rect_of(target)
    x, y = rect.x + dx, rect.y + dy
    controller.click(ClickStep(x, y))
    qtbot.waitUntil(lambda: bool(target.clicks), timeout=2000)
    name, cx, cy = target.clicks[0]
    assert name == "left"
    # Qt converte para pixels lógicos e de volta; aceitamos 1 px físico de arredondamento.
    assert abs(cx - x) <= 1 and abs(cy - y) <= 1, f"clique em {cx},{cy}; esperado {x},{y}"


def test_right_middle_and_double_clicks(qtbot: QtBot, target: Target, controller: InputController) -> None:
    rect = physical_rect_of(target)
    cx, cy = rect.x + rect.width // 2, rect.y + rect.height // 2
    controller.click(ClickStep(cx, cy, MouseButton.RIGHT))
    controller.click(ClickStep(cx, cy, MouseButton.MIDDLE))
    qtbot.waitUntil(lambda: len(target.clicks) == 2, timeout=2000)
    assert [c[0] for c in target.clicks] == ["right", "middle"]
    controller.click(ClickStep(cx, cy, double=True))
    qtbot.waitUntil(lambda: target.doubles == 1, timeout=2000)


def test_key_combo_reaches_focused_window(qtbot: QtBot, target: Target, controller: InputController) -> None:
    rect = physical_rect_of(target)
    controller.click(ClickStep(rect.x + 10, rect.y + 10))  # um clique real dá o foco à janela
    qtbot.waitUntil(lambda: bool(target.clicks), timeout=2000)
    qtbot.wait(100)
    combo = parse_combo("Ctrl+Shift+L")
    if taken_by_another_program(combo):
        pytest.skip("Ctrl+Shift+L é atalho global de outro programa nesta máquina")
    controller.press(combo)
    qtbot.waitUntil(lambda: any(k == Qt.Key.Key_L for k, _ in target.keys), timeout=2000)
    key, modifiers = next(item for item in target.keys if item[0] == Qt.Key.Key_L)
    assert modifiers & Qt.KeyboardModifier.ControlModifier and modifiers & Qt.KeyboardModifier.ShiftModifier
    assert not controller.anything_held


# -- de ponta a ponta com o aplicativo -------------------------------------------------------------
@pytest.fixture
def app_window(qtbot: QtBot, tmp_path: Path) -> Iterator[object]:
    from vanthor.app import build_context
    from vanthor.ui.main_window import MainWindow
    from vanthor.ui.theme.stylesheet import apply_theme

    apply_theme(QApplication.instance())  # type: ignore[arg-type]
    db = Database(tmp_path / "input.db")
    ctx = build_context(db)
    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.showMinimized()
    qtbot.wait(100)
    original = InputController(Win32Input()).cursor()
    yield window
    ctx.session.shutdown()
    db.close()
    InputController(Win32Input()).move(*original)


def test_color_appears_and_rule_clicks_target(qtbot: QtBot, app_window: object, target: Target) -> None:
    ctx = app_window._ctx  # type: ignore[attr-defined]
    profile = ctx.profiles.active().id
    lamp = Lamp(QColor("#30343A"))
    qtbot.addWidget(lamp)
    geo = QGuiApplication.primaryScreen().geometry()
    lamp.setGeometry(geo.x() + 300, geo.y() + 320, 40, 30)
    lamp.show()
    qtbot.waitExposed(lamp)
    qtbot.wait(150)

    zone = ctx.zones.create(profile, physical_rect_of(lamp), "z", interval_ms=50)
    trect = physical_rect_of(target)
    point = (trect.x + 30, trect.y + 30)
    action = ActionSpec(ActionKind.CLICK, (ClickStep(*point),))
    rule = ctx.rules.create(profile, "Clicar no alvo", zone.id, ColorCondition(rgb(QColor("#22C55E"))),
                            TriggerSettings(confirm_frames=2), action)
    before = InputController(Win32Input()).cursor()
    assert ctx.session.start()
    qtbot.wait(300)
    assert target.clicks == []

    lamp.set_color(QColor("#22C55E"))
    qtbot.waitUntil(lambda: len(target.clicks) == 1, timeout=3000)
    _, cx, cy = target.clicks[0]
    assert abs(cx - point[0]) <= 1 and abs(cy - point[1]) <= 1
    qtbot.waitUntil(lambda: ctx.rules.totals(profile).clicks == 1, timeout=2000)
    assert InputController(Win32Input()).cursor() == before  # o mouse voltou para onde estava
    ctx.session.stop()
    assert ctx.rules.get(rule.id).status is RuleStatus.ACTIVE


def test_emergency_key_interrupts_sequence_midway(qtbot: QtBot, app_window: object, target: Target) -> None:
    import ctypes

    ctx = app_window._ctx  # type: ignore[attr-defined]
    profile = ctx.profiles.active().id
    lamp = Lamp(QColor("#22C55E"))
    qtbot.addWidget(lamp)
    geo = QGuiApplication.primaryScreen().geometry()
    lamp.setGeometry(geo.x() + 300, geo.y() + 480, 40, 30)
    lamp.show()
    qtbot.waitExposed(lamp)
    qtbot.wait(150)
    zone = ctx.zones.create(profile, physical_rect_of(lamp), "z", interval_ms=50)
    trect = physical_rect_of(target)
    seq = ActionSpec(
        ActionKind.SEQUENCE,
        (ClickStep(trect.x + 20, trect.y + 20), WaitStep(5000), ClickStep(trect.x + 60, trect.y + 60)),
    )
    ctx.rules.create(profile, "Sequência longa", zone.id, ColorCondition(rgb(QColor("#22C55E"))),
                     TriggerSettings(confirm_frames=1), seq)
    notices: list[str] = []
    ctx.session.event_received.connect(lambda e: notices.append(e.message) if e.kind is EventKind.NOTICE else None)
    assert ctx.session.start()
    qtbot.waitUntil(lambda: len(target.clicks) == 1, timeout=3000)

    user32 = ctypes.windll.user32
    user32.keybd_event(0x1B, 0, 0, 0)  # Esc de verdade: tecla de emergência
    user32.keybd_event(0x1B, 0, 2, 0)
    qtbot.waitUntil(lambda: any("interrompida" in n for n in notices), timeout=2000)
    qtbot.wait(300)
    assert len(target.clicks) == 1  # o segundo clique da sequência nunca aconteceu
    assert ctx.rules.totals(profile).clicks == 1


def test_key_action_in_rule(qtbot: QtBot, app_window: object, target: Target) -> None:
    ctx = app_window._ctx  # type: ignore[attr-defined]
    profile = ctx.profiles.active().id
    lamp = Lamp(QColor("#22C55E"))
    qtbot.addWidget(lamp)
    geo = QGuiApplication.primaryScreen().geometry()
    lamp.setGeometry(geo.x() + 300, geo.y() + 560, 40, 30)
    lamp.show()
    qtbot.waitExposed(lamp)
    qtbot.wait(150)
    zone = ctx.zones.create(profile, physical_rect_of(lamp), "z", interval_ms=50)
    trect = physical_rect_of(target)
    # Clica no alvo (para dar foco) e pressiona F7: sequência real de clique + tecla.
    seq = ActionSpec(ActionKind.SEQUENCE, (ClickStep(trect.x + 5, trect.y + 5), WaitStep(80), KeyStep("F7")))
    ctx.rules.create(profile, "Clique e tecla", zone.id, ColorCondition(rgb(QColor("#22C55E"))),
                     TriggerSettings(confirm_frames=1), seq)
    assert ctx.session.start()
    qtbot.waitUntil(lambda: any(k == Qt.Key.Key_F7 for k, _ in target.keys), timeout=3000)
    ctx.session.stop()




def test_detects_combo_taken_by_another_program() -> None:
    import ctypes

    user32 = ctypes.windll.user32
    combo = parse_combo("Ctrl+Alt+F9")
    assert user32.RegisterHotKey(None, 0x7A7A, 0x2 | 0x1 | 0x4000, 0x78), "Ctrl+Alt+F9 já ocupado nesta máquina"
    try:
        assert taken_by_another_program(combo)
    finally:
        user32.UnregisterHotKey(None, 0x7A7A)
    assert not taken_by_another_program(combo)
