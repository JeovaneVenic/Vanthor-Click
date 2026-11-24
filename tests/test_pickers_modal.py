"""Regressão: escolher ponto/cor na tela a partir de uma janela MODAL (o editor de automação).

Bug relatado: dentro do editor, o seletor de ponto não aceitava o clique; só o Esc funcionava, e
ele fechava o editor. Causa: a janela modal fazia o Qt bloquear o mouse nas sobreposições.
Estes testes usam clique e tecla REAIS do sistema: eventos simulados direto no widget pulam a
trava de modalidade do Qt e esconderiam o problema.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402
from pytestqt.qtbot import QtBot  # noqa: E402

from vanthor.actions.controller import InputController  # noqa: E402
from vanthor.actions.models import ClickStep  # noqa: E402
from vanthor.capture.geometry import ScreenRect  # noqa: E402
from vanthor.persistence.database import Database  # noqa: E402
from vanthor.winsys.input import InputError  # noqa: E402

from .native import REAL_DESKTOP, press_key, real_input  # noqa: E402

pytestmark = pytest.mark.skipif(not REAL_DESKTOP, reason="entrada real (Windows ou Linux/X11)")


@pytest.fixture
def ctx(qtbot: QtBot, tmp_path: Path) -> Iterator[object]:
    from vanthor.app import build_context
    from vanthor.ui.theme.stylesheet import apply_theme

    apply_theme(QApplication.instance())  # type: ignore[arg-type]
    db = Database(tmp_path / "modal.db")
    context = build_context(db)
    controller = InputController(real_input())
    original = controller.cursor()
    yield context
    context.session.shutdown()
    db.close()
    try:
        controller.move(*original)  # cortesia: devolve o mouse de quem roda os testes
    except InputError:
        if sys.platform != "win32":
            raise
        u = ctypes.WinDLL("user32", use_last_error=True)
        h = u.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(h, buf, 256)
        print(f"[diagnóstico] SetCursorPos recusado; primeiro plano: {buf.value!r}; "
              f"desktop interativo: {bool(u.OpenInputDesktop(0, False, 0x100))}")


def _primary_point(dx: int, dy: int) -> tuple[int, int]:
    from vanthor.ui.screens import monitor_maps

    primary = next(m for m in monitor_maps() if m.primary)
    return primary.physical.x + dx, primary.physical.y + dy


def _open_modal_editor(qtbot: QtBot, ctx: object) -> object:
    from vanthor.ui.dialogs.rule_editor import RuleEditorDialog

    profile = ctx.profiles.active().id  # type: ignore[attr-defined]
    ctx.zones.create(profile, ScreenRect(*_primary_point(40, 40), 30, 30), "m")  # type: ignore[attr-defined]
    dialog = RuleEditorDialog(ctx, None)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    # Igual ao exec() do aplicativo: modal para o app inteiro.
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.show()
    qtbot.waitExposed(dialog)
    dialog._go(2)  # noqa: SLF001
    return dialog


def test_click_point_can_be_picked_from_modal_editor(qtbot: QtBot, ctx: object) -> None:
    dialog = _open_modal_editor(qtbot, ctx)
    editor = dialog._action_editor  # type: ignore[attr-defined]  # noqa: SLF001
    picked: list[tuple[int, int]] = []
    editor.pick_point(lambda x, y: picked.append((x, y)))
    qtbot.waitUntil(lambda: editor._picker is not None and bool(editor._picker._overlays), timeout=2000)  # noqa: SLF001
    qtbot.wait(300)

    target = _primary_point(420, 360)
    InputController(real_input()).click(ClickStep(*target))  # clique REAL
    qtbot.waitUntil(lambda: bool(picked), timeout=3000)

    assert abs(picked[0][0] - target[0]) <= 1 and abs(picked[0][1] - target[1]) <= 1
    assert dialog.isVisible() and dialog.result() == 0  # o editor continua aberto  # type: ignore[attr-defined]
    assert dialog.windowOpacity() == 1.0  # e voltou a aparecer  # type: ignore[attr-defined]


def test_escape_cancels_only_the_picker_not_the_editor(qtbot: QtBot, ctx: object) -> None:
    dialog = _open_modal_editor(qtbot, ctx)
    editor = dialog._action_editor  # type: ignore[attr-defined]  # noqa: SLF001
    editor.pick_point(lambda x, y: None)
    qtbot.waitUntil(lambda: editor._picker is not None and bool(editor._picker._overlays), timeout=2000)  # noqa: SLF001
    qtbot.wait(300)

    press_key(0x1B)  # Esc REAL
    qtbot.waitUntil(lambda: editor._picker is None, timeout=3000)  # noqa: SLF001
    qtbot.wait(200)
    assert dialog.isVisible() and dialog.result() == 0  # type: ignore[attr-defined]


def test_eyedropper_works_from_modal_color_dialog(qtbot: QtBot, ctx: object) -> None:
    from vanthor.ui.dialogs.color_test import ColorTestDialog

    profile = ctx.profiles.active().id  # type: ignore[attr-defined]
    zone = ctx.zones.create(profile, ScreenRect(*_primary_point(40, 40), 30, 30), "m")  # type: ignore[attr-defined]
    dialog = ColorTestDialog(ctx, zone, save_draft=False)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.show()
    qtbot.waitExposed(dialog)
    before = dialog.condition().color
    dialog._start_eyedropper()  # noqa: SLF001
    qtbot.waitUntil(lambda: dialog._eyedropper is not None and bool(dialog._eyedropper._overlays), timeout=2000)  # noqa: SLF001
    qtbot.wait(300)

    # Clique REAL longe dos cantos: no Linux, painel e bandeja do XFCE ficam nas bordas da tela.
    InputController(real_input()).click(ClickStep(*_primary_point(400, 300)))
    qtbot.waitUntil(lambda: dialog._eyedropper is None, timeout=3000)  # noqa: SLF001
    assert dialog.isVisible() and dialog.result() == QDialog.DialogCode.Rejected.value == 0
    del before
