"""Integração da Fase 3: conta-gotas e "Testar cor" com janelas e captura reais."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
from pytestqt.qtbot import QtBot  # noqa: E402

from vanthor.persistence.database import Database  # noqa: E402
from vanthor.vision.color import ColorCondition, ColorSpace, MatchMode  # noqa: E402

from .test_capture_dpi import PATTERN, pattern, physical_rect_of  # noqa: E402, F401

from .native import REAL_DESKTOP  # noqa: E402

pytestmark = pytest.mark.skipif(not REAL_DESKTOP, reason="tela real (Windows ou Linux/X11)")
PATTERN_RGB = (PATTERN.red(), PATTERN.green(), PATTERN.blue())


@pytest.fixture
def ctx(qtbot: QtBot, tmp_path: Path) -> Iterator[object]:
    from vanthor.app import build_context
    from vanthor.ui.theme.stylesheet import apply_theme

    apply_theme(QApplication.instance())  # type: ignore[arg-type]
    db = Database(tmp_path / "color.db")
    context = build_context(db)
    yield context
    context.session.shutdown()
    db.close()


def test_eyedropper_picks_exact_pixel_color(qtbot: QtBot, pattern: QWidget) -> None:
    from vanthor.ui.overlays.eyedropper import Eyedropper

    dropper = Eyedropper()
    dropper.start()
    overlay = next(o for o in dropper._overlays if o.monitor.primary)  # noqa: SLF001
    qtbot.waitExposed(overlay)
    center = pattern.geometry().center() - overlay.geometry().topLeft()
    qtbot.mouseMove(overlay, pos=center)
    with qtbot.waitSignal(dropper.picked, timeout=2000) as picked:
        qtbot.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=center)
    rgb, x, y = picked.args
    assert rgb == PATTERN_RGB
    assert physical_rect_of(pattern).contains_point(x, y)


def test_eyedropper_escape_cancels(qtbot: QtBot) -> None:
    from vanthor.ui.overlays.eyedropper import Eyedropper

    dropper = Eyedropper()
    dropper.start()
    overlay = dropper._overlays[0]  # noqa: SLF001
    qtbot.waitExposed(overlay)
    with qtbot.waitSignal(dropper.cancelled, timeout=1000):
        qtbot.keyClick(overlay, Qt.Key.Key_Escape)


def test_color_test_dialog_detects_live_and_saves_draft(qtbot: QtBot, ctx: object, pattern: QWidget) -> None:
    from vanthor.ui.dialogs.color_test import ColorTestDialog

    zones = ctx.zones  # type: ignore[attr-defined]
    profile = ctx.profiles.active()  # type: ignore[attr-defined]
    rect = physical_rect_of(pattern)
    zone = zones.create(profile.id, rect, "x")

    dialog = ColorTestDialog(ctx, zone)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    pattern.raise_()  # o diálogo não pode cobrir a janela de teste
    qtbot.wait(100)
    dialog._tick()  # noqa: SLF001

    # Sem ajuste salvo, começa pela cor predominante da zona (aqui, sólida).
    assert dialog.condition().color == PATTERN_RGB
    assert dialog._result_title.text() == "Cor encontrada"  # noqa: SLF001
    assert "100,0% da zona" in dialog._result_detail.text()  # noqa: SLF001

    dialog.set_color((255, 0, 255))
    dialog._tick()  # noqa: SLF001
    assert dialog._result_title.text() == "Cor não encontrada"  # noqa: SLF001

    dialog.accept()  # concluir salva na hora, sem esperar o atraso
    saved = zones.color_draft(zone.id)
    assert saved is not None and saved.color == (255, 0, 255)


def test_color_test_dialog_restores_saved_draft(qtbot: QtBot, ctx: object) -> None:
    from vanthor.capture.geometry import ScreenRect
    from vanthor.ui.dialogs.color_test import ColorTestDialog

    zones = ctx.zones  # type: ignore[attr-defined]
    zone = zones.create(ctx.profiles.active().id, ScreenRect(10, 10, 20, 20), "x")  # type: ignore[attr-defined]
    saved = ColorCondition((10, 20, 30), ColorSpace.HSV, 33, MatchMode.PERCENT, min_percent=12.5)
    zones.save_color_draft(zone.id, saved)
    dialog = ColorTestDialog(ctx, zone)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    assert dialog.condition() == saved
    dialog.reject()


def test_switching_color_space_resets_tolerance_to_its_scale(qtbot: QtBot, ctx: object) -> None:
    from vanthor.capture.geometry import ScreenRect
    from vanthor.ui.dialogs.color_test import ColorTestDialog

    zone = ctx.zones.create(ctx.profiles.active().id, ScreenRect(10, 10, 20, 20), "x")  # type: ignore[attr-defined]
    ctx.zones.save_color_draft(zone.id, ColorCondition((1, 2, 3), tolerance=200))  # type: ignore[attr-defined]
    dialog = ColorTestDialog(ctx, zone)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    dialog._space.changed.emit(ColorSpace.HSV.value)  # noqa: SLF001
    dialog._space.set_value(ColorSpace.HSV.value)  # noqa: SLF001
    condition = dialog.condition()  # 200 seria inválido em HSV (máximo 100)
    assert condition.space is ColorSpace.HSV and condition.tolerance <= 100
    dialog.reject()
