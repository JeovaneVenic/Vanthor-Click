"""Aceite da Fase 4: regras independentes rodando juntas sobre a tela real."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
from pytestqt.qtbot import QtBot  # noqa: E402

from vanthor.actions.models import ClickStep  # noqa: E402
from vanthor.core.events import EventKind  # noqa: E402
from vanthor.persistence.database import Database  # noqa: E402
from vanthor.rules.models import ActionSpec, FireMode, RuleStatus, TriggerSettings  # noqa: E402
from vanthor.vision.color import ColorCondition  # noqa: E402

from .test_capture_dpi import physical_rect_of  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="tela real do Windows")

GREEN, RED, GRAY = QColor("#22C55E"), QColor("#EF4444"), QColor("#30343A")


def rgb(c: QColor) -> tuple[int, int, int]:
    return c.red(), c.green(), c.blue()


class Lamp(QWidget):
    """Janela de teste que muda de cor como um botão de outro programa."""

    def __init__(self, color: QColor) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.color = color

    def set_color(self, color: QColor) -> None:
        self.color = color
        self.repaint()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self.color)
        p.end()


@pytest.fixture
def app_window(qtbot: QtBot, tmp_path: Path) -> Iterator[object]:
    from vanthor.app import build_context
    from vanthor.ui.main_window import MainWindow
    from vanthor.ui.theme.stylesheet import apply_theme

    apply_theme(QApplication.instance())  # type: ignore[arg-type]
    db = Database(tmp_path / "e2e.db")
    ctx = build_context(db)
    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.showMinimized()  # precisa existir (tecla de emergência), mas não pode cobrir as lâmpadas
    qtbot.wait(100)
    yield window
    ctx.session.shutdown()
    db.close()


def lamp_at(qtbot: QtBot, x: int, color: QColor) -> Lamp:
    lamp = Lamp(color)
    qtbot.addWidget(lamp)
    geo = QGuiApplication.primaryScreen().geometry()
    lamp.setGeometry(geo.x() + x, geo.y() + 260, 40, 30)
    lamp.show()
    qtbot.waitExposed(lamp)
    return lamp


def test_two_independent_rules_fire_and_rearm_on_real_screen(
    qtbot: QtBot, app_window: object, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = app_window._ctx  # type: ignore[attr-defined]
    profile = ctx.profiles.active().id
    lamp_a, lamp_b = lamp_at(qtbot, 300, GRAY), lamp_at(qtbot, 420, GRAY)
    qtbot.wait(150)
    zone_a = ctx.zones.create(profile, physical_rect_of(lamp_a), "a", interval_ms=50)
    zone_b = ctx.zones.create(profile, physical_rect_of(lamp_b), "b", interval_ms=50)
    trigger = TriggerSettings(FireMode.ON_APPEAR, confirm_frames=2, release_frames=2, cooldown_ms=0)
    rule_a = ctx.rules.create(profile, "Verde em A", zone_a.id, ColorCondition(rgb(GREEN)), trigger, ActionSpec())
    rule_b = ctx.rules.create(profile, "Vermelho em B", zone_b.id, ColorCondition(rgb(RED)), trigger, ActionSpec())

    fired: list[int] = []
    ctx.session.event_received.connect(lambda e: fired.append(e.rule_id) if e.kind is EventKind.ACTION else None)
    assert ctx.session.start()

    qtbot.wait(300)
    assert fired == []  # nada verde nem vermelho ainda

    lamp_a.set_color(GREEN)
    qtbot.waitUntil(lambda: fired == [rule_a.id], timeout=3000)
    qtbot.wait(400)
    assert fired == [rule_a.id]  # continua verde: não repete ("ao aparecer")

    lamp_b.set_color(RED)
    qtbot.waitUntil(lambda: fired == [rule_a.id, rule_b.id], timeout=3000)

    lamp_a.set_color(GRAY)  # some...
    qtbot.wait(400)
    lamp_a.set_color(GREEN)  # ...e volta: rearma e age de novo
    qtbot.waitUntil(lambda: fired.count(rule_a.id) == 2, timeout=3000)

    ctx.session.stop()
    # Parar libera a captura sem nenhum erro (ex.: ReleaseDC fora da thread dona do contexto).
    problems = [r for r in caplog.records if r.levelno >= 30 and r.name.startswith("vanthor")]
    assert problems == [], [r.getMessage() for r in problems]
    totals = ctx.rules.totals(profile)
    assert totals.detections == 3
    assert ctx.rules.get(rule_a.id).executions_total == 2


def test_paused_rule_is_ignored_and_change_applies_while_running(qtbot: QtBot, app_window: object) -> None:
    ctx = app_window._ctx  # type: ignore[attr-defined]
    profile = ctx.profiles.active().id
    lamp = lamp_at(qtbot, 540, GREEN)
    qtbot.wait(150)
    zone = ctx.zones.create(profile, physical_rect_of(lamp), "z", interval_ms=50)
    rule = ctx.rules.create(
        profile, "Pausada", zone.id, ColorCondition(rgb(GREEN)), TriggerSettings(confirm_frames=1), ActionSpec(),
        RuleStatus.PAUSED,
    )
    fired: list[int] = []
    ctx.session.event_received.connect(lambda e: fired.append(e.rule_id) if e.kind is EventKind.ACTION else None)
    assert ctx.session.start()
    qtbot.wait(400)
    assert fired == []

    ctx.rules.set_status(rule.id, RuleStatus.ACTIVE)  # ligar durante a automação vale na hora
    ctx.session.refresh_watch()
    qtbot.waitUntil(lambda: fired == [rule.id], timeout=3000)
    ctx.session.stop()


def test_editor_creates_rule_through_the_three_steps(qtbot: QtBot, app_window: object) -> None:
    from vanthor.capture.geometry import ScreenRect
    from vanthor.ui.dialogs.rule_editor import RuleEditorDialog

    ctx = app_window._ctx  # type: ignore[attr-defined]
    profile = ctx.profiles.active().id
    zone = ctx.zones.create(profile, ScreenRect(10, 10, 30, 30), "z")
    ctx.zones.save_color_draft(zone.id, ColorCondition((1, 2, 3), tolerance=7))

    dialog = RuleEditorDialog(ctx, None)
    qtbot.addWidget(dialog)
    assert dialog._name.text() == "Automação 01"  # noqa: SLF001
    assert dialog._condition() == ColorCondition((1, 2, 3), tolerance=7)  # usa o ajuste do "Testar cor"  # noqa: SLF001

    dialog._name.setText("")  # noqa: SLF001
    dialog._advance()  # noqa: SLF001
    assert dialog._step == 0 and "nome" in dialog._error.text()  # noqa: SLF001

    dialog._name.setText("Minha regra")  # noqa: SLF001
    dialog._advance()  # noqa: SLF001
    dialog._mode.set_value(FireMode.CONTINUOUS.value)  # noqa: SLF001
    dialog._cooldown.setValue(500)  # noqa: SLF001
    dialog._confirm.setValue(3)  # noqa: SLF001
    dialog._advance()  # noqa: SLF001
    assert dialog._step == 2  # noqa: SLF001
    dialog._advance()  # padrão é "Clicar": sem ponto escolhido, não salva  # noqa: SLF001
    assert dialog._step == 2 and "ponto do clique" in dialog._error.text()  # noqa: SLF001
    dialog._action_editor._set_click_point(700, 300)  # noqa: SLF001
    assert "clique esquerdo em 700, 300" in dialog._summary.text()  # noqa: SLF001
    dialog._advance()  # salvar  # noqa: SLF001

    saved = ctx.rules.list(profile)
    assert len(saved) == 1
    rule = saved[0]
    assert (rule.name, rule.zone_id, rule.trigger.mode, rule.trigger.cooldown_ms, rule.trigger.confirm_frames) == (
        "Minha regra", zone.id, FireMode.CONTINUOUS, 500, 3,
    )
    assert rule.action.steps == (ClickStep(700, 300),)


def test_editor_requires_a_zone(qtbot: QtBot, app_window: object) -> None:
    from vanthor.ui.dialogs.rule_editor import RuleEditorDialog

    dialog = RuleEditorDialog(app_window._ctx, None)  # type: ignore[attr-defined]
    qtbot.addWidget(dialog)
    dialog._advance()  # noqa: SLF001
    assert dialog._step == 0 and "zona" in dialog._error.text()  # noqa: SLF001
