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


def _settle(qtbot: QtBot, overlays: list[object]) -> None:
    """Espera as sobreposições estarem de fato na tela antes da entrada REAL.

    No primeiro uso de um processo, o Windows pode levar mais que 300 ms para desenhar uma janela
    translúcida por cima de tudo; um clique antes disso cai na janela de baixo (instabilidade antiga).
    """
    for overlay in overlays:
        qtbot.waitExposed(overlay)  # type: ignore[arg-type]
    qtbot.wait(600)


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
    _settle(qtbot, editor._picker._overlays)  # noqa: SLF001

    target = _primary_point(420, 360)
    InputController(real_input()).click(ClickStep(*target))  # clique REAL: leva o alvo até lá

    def target_near() -> bool:  # 1 px de folga: arredondamento da escala (ex.: 125%)
        current = editor._picker.target  # noqa: SLF001
        return current is not None and abs(current[1] - target[0]) <= 1 and abs(current[2] - target[1]) <= 1

    qtbot.waitUntil(target_near, timeout=3000)
    assert picked == []  # o clique só posiciona; quem confirma é o Enter (ou "Confirmar ponto")
    press_key(0x0D)  # Enter REAL
    qtbot.waitUntil(lambda: bool(picked), timeout=3000)

    assert abs(picked[0][0] - target[0]) <= 1 and abs(picked[0][1] - target[1]) <= 1
    assert dialog.isVisible() and dialog.result() == 0  # o editor continua aberto  # type: ignore[attr-defined]
    assert dialog.windowOpacity() == 1.0  # e voltou a aparecer  # type: ignore[attr-defined]


def test_escape_cancels_only_the_picker_not_the_editor(qtbot: QtBot, ctx: object) -> None:
    dialog = _open_modal_editor(qtbot, ctx)
    editor = dialog._action_editor  # type: ignore[attr-defined]  # noqa: SLF001
    editor.pick_point(lambda x, y: None)
    qtbot.waitUntil(lambda: editor._picker is not None and bool(editor._picker._overlays), timeout=2000)  # noqa: SLF001
    _settle(qtbot, editor._picker._overlays)  # noqa: SLF001

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


# -- alvo arrastável (acesso remoto) ----------------------------------------------------------------
def test_click_target_appears_ready_and_can_be_dragged(qtbot: QtBot, ctx: object) -> None:
    """O alvo já aparece na tela: arrastar (mouse REAL), ajustar com as setas e confirmar com Enter."""
    from vanthor.actions.models import MouseButton

    dialog = _open_modal_editor(qtbot, ctx)
    editor = dialog._action_editor  # type: ignore[attr-defined]  # noqa: SLF001
    picked: list[tuple[int, int]] = []
    editor.pick_point(lambda x, y: picked.append((x, y)), (_primary_point(300, 300)))
    qtbot.waitUntil(lambda: editor._picker is not None and bool(editor._picker._overlays), timeout=2000)  # noqa: SLF001
    _settle(qtbot, editor._picker._overlays)  # noqa: SLF001
    picker = editor._picker  # noqa: SLF001
    assert picker.target is not None and picker.target[1:] == _primary_point(300, 300)  # começa no ponto atual

    backend = real_input()
    start, end = _primary_point(302, 301), _primary_point(500, 420)
    backend.set_cursor(*start)
    qtbot.wait(80)
    backend.mouse(MouseButton.LEFT, True)  # pega o alvo (perto dele)...
    qtbot.wait(60)
    for step in range(1, 6):  # ...arrasta em etapas, como uma pessoa...
        backend.set_cursor(start[0] + (end[0] - start[0]) * step // 5, start[1] + (end[1] - start[1]) * step // 5)
        qtbot.wait(40)
    backend.mouse(MouseButton.LEFT, False)  # ...e solta: ainda NÃO confirma
    qtbot.waitUntil(lambda: abs(picker.target[1] - end[0]) <= 1 and abs(picker.target[2] - end[1]) <= 1, timeout=2000)
    assert picked == []

    dropped = picker.target[1:]
    press_key(0x27)  # seta para a direita: +1 px
    press_key(0x28)  # seta para baixo: +1 px
    qtbot.waitUntil(lambda: picker.target[1:] == (dropped[0] + 1, dropped[1] + 1), timeout=2000)
    press_key(0x0D)  # Enter confirma
    qtbot.waitUntil(lambda: bool(picked), timeout=3000)
    assert picked == [(dropped[0] + 1, dropped[1] + 1)]  # as setas movem 1 pixel físico exato
    assert dialog.isVisible() and dialog.result() == 0  # type: ignore[attr-defined]


def test_new_click_target_starts_on_screen_center(qtbot: QtBot, ctx: object) -> None:
    from vanthor.ui.screens import monitor_maps

    dialog = _open_modal_editor(qtbot, ctx)
    editor = dialog._action_editor  # type: ignore[attr-defined]  # noqa: SLF001
    InputController(real_input()).move(*_primary_point(600, 400))
    editor.pick_point(lambda x, y: None)
    qtbot.waitUntil(lambda: editor._picker is not None and bool(editor._picker._overlays), timeout=2000)  # noqa: SLF001
    primary = next(m for m in monitor_maps() if m.primary)
    monitor, x, y = editor._picker.target  # noqa: SLF001
    assert monitor.name == primary.name
    assert (x, y) == (primary.physical.x + primary.physical.width // 2, primary.physical.y + primary.physical.height // 2)
    press_key(0x1B)
    qtbot.waitUntil(lambda: editor._picker is None, timeout=3000)  # noqa: SLF001


def test_new_zone_starts_drawn_and_moves_with_real_keys(qtbot: QtBot) -> None:
    """Zona nova já desenhada no centro: setas (teclado REAL) movem e Enter confirma, sem desenhar."""
    from vanthor.ui.overlays.zone_selector import ZoneSelector
    from vanthor.ui.screens import monitor_maps

    InputController(real_input()).move(*_primary_point(600, 400))
    selector = ZoneSelector("#34D399", "Teste")
    primary = next(m for m in monitor_maps() if m.primary)
    first = selector.rect
    assert first is not None and selector.monitor is not None and selector.monitor.name == primary.name
    assert abs((first.x + first.width / 2) - (primary.physical.x + primary.physical.width / 2)) <= 1
    assert abs((first.y + first.height / 2) - (primary.physical.y + primary.physical.height / 2)) <= 1
    assert first.width == round(200 * primary.scale) and first.height == round(120 * primary.scale)

    selector.start()
    overlay = next(o for o in selector._overlays if o.monitor.primary)  # noqa: SLF001
    _settle(qtbot, selector._overlays)  # noqa: SLF001
    results: list[ScreenRect] = []
    selector.selected.connect(lambda rect, _name: results.append(rect))
    press_key(0x25)  # seta para a esquerda
    press_key(0x26)  # seta para cima
    press_key(0x0D)  # Enter
    qtbot.waitUntil(lambda: bool(results), timeout=3000)
    assert results == [ScreenRect(first.x - 1, first.y - 1, first.width, first.height)]


def test_new_zone_can_be_dragged_with_real_mouse(qtbot: QtBot) -> None:
    from vanthor.actions.models import MouseButton
    from vanthor.ui.overlays.zone_selector import ZoneSelector

    InputController(real_input()).move(*_primary_point(600, 400))
    selector = ZoneSelector("#34D399", "Teste")
    first = selector.rect
    assert first is not None
    selector.start()
    overlay = next(o for o in selector._overlays if o.monitor.primary)  # noqa: SLF001
    _settle(qtbot, selector._overlays)  # noqa: SLF001

    backend = real_input()
    grab = (first.x + first.width // 2, first.y + first.height // 2)  # meio da zona: arrastar move
    backend.set_cursor(*grab)
    qtbot.wait(80)
    backend.mouse(MouseButton.LEFT, True)
    qtbot.wait(60)
    for step in range(1, 6):
        backend.set_cursor(grab[0] - 30 * step, grab[1] - 20 * step)
        qtbot.wait(40)
    backend.mouse(MouseButton.LEFT, False)
    def moved() -> bool:
        # Folga de 15 px: o teste "teletransporta" o cursor real, e o Windows às vezes entrega o
        # aperto do botão com a posição de um instante antes. O ajuste fino é das setas (outro teste).
        r = selector.rect
        return r is not None and abs(r.x - (first.x - 150)) <= 15 and abs(r.y - (first.y - 100)) <= 15

    qtbot.waitUntil(moved, timeout=2000)
    assert (selector.rect.width, selector.rect.height) == (first.width, first.height)  # mover não muda o tamanho
    selector.cancel()
