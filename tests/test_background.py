"""Segundo plano: fechar a janela mantém o Vanthor rodando na bandeja, com a emergência valendo."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402
from pytestqt.qtbot import QtBot  # noqa: E402

from vanthor.core.engine import EngineState  # noqa: E402
from vanthor.persistence.database import Database  # noqa: E402
from vanthor.persistence.repositories import AppSettings  # noqa: E402

from .native import REAL_DESKTOP, press_key  # noqa: E402

pytestmark = pytest.mark.skipif(not REAL_DESKTOP, reason="bandeja e atalho global reais (Windows ou Linux/X11)")


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> Iterator[object]:
    from vanthor.app import build_context
    from vanthor.ui.main_window import MainWindow
    from vanthor.ui.theme.stylesheet import apply_theme

    apply_theme(QApplication.instance())  # type: ignore[arg-type]
    db = Database(tmp_path / "bg.db")
    ctx = build_context(db)
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    yield win
    ctx.session.shutdown()
    win._indicator.close()  # noqa: SLF001
    win._tray.hide()  # noqa: SLF001
    db.close()


def test_closing_window_keeps_automation_running(qtbot: QtBot, window: object) -> None:
    ctx = window._ctx  # type: ignore[attr-defined]
    if not window._tray.available:  # type: ignore[attr-defined]  # noqa: SLF001
        pytest.skip("bandeja do sistema indisponível")
    assert ctx.session.start()
    window.close()  # type: ignore[attr-defined]
    qtbot.wait(150)
    assert not window.isVisible()  # type: ignore[attr-defined]
    assert ctx.session.state is EngineState.RUNNING  # continua vigiando
    assert window._indicator.isVisible()  # type: ignore[attr-defined]  # noqa: SLF001

    window.bring_to_front()  # type: ignore[attr-defined]  # clique no ícone da bandeja
    qtbot.waitUntil(window.isVisible, timeout=2000)  # type: ignore[attr-defined]
    ctx.session.stop()


def test_emergency_key_works_with_window_hidden(qtbot: QtBot, window: object) -> None:
    ctx = window._ctx  # type: ignore[attr-defined]
    window.hide()  # type: ignore[attr-defined]
    qtbot.wait(100)
    assert ctx.session.start()
    press_key(0x1B)
    qtbot.waitUntil(lambda: ctx.session.state is EngineState.STOPPED, timeout=3000)


def test_start_works_without_window_ever_shown(qtbot: QtBot, tmp_path: Path) -> None:
    """Início com o Windows em segundo plano: a janela nunca aparece, mas a emergência precisa existir."""
    from vanthor.app import build_context
    from vanthor.ui.main_window import MainWindow

    db = Database(tmp_path / "hidden.db")
    ctx = build_context(db)
    win = MainWindow(ctx)  # nunca chamamos show()
    try:
        assert ctx.session.start()
        ctx.session.stop()
    finally:
        ctx.session.shutdown()
        win._indicator.close()  # noqa: SLF001
        win._tray.hide()  # noqa: SLF001
        win.deleteLater()
        db.close()


def test_closing_with_background_off_really_stops(qtbot: QtBot, window: object) -> None:
    ctx = window._ctx  # type: ignore[attr-defined]
    ctx.settings.save(AppSettings(run_in_background=False))
    assert ctx.session.start()
    window.close()  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: ctx.session.state is EngineState.STOPPED, timeout=3000)


def test_quit_from_tray_stops_everything(qtbot: QtBot, window: object, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = window._ctx  # type: ignore[attr-defined]
    quits: list[bool] = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(True)))
    assert ctx.session.start()
    window.quit_app()  # type: ignore[attr-defined]
    assert ctx.session.state is EngineState.STOPPED
    assert quits
