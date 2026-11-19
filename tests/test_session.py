"""Testes de integração da sessão com a janela real e o atalho global do Windows."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from pytestqt.qtbot import QtBot  # noqa: E402

from vanthor.core.engine import EngineState  # noqa: E402
from vanthor.persistence.database import Database  # noqa: E402
from vanthor.persistence.repositories import AppSettings, SettingsRepository  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="atalho global é do Windows")

VK_ESCAPE = 0x1B
VK_F11 = 0x7A
KEYEVENTF_KEYUP = 0x0002


def press(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> Iterator[object]:
    from vanthor.app import build_context
    from vanthor.ui.main_window import MainWindow
    from vanthor.ui.theme.stylesheet import apply_theme

    from PySide6.QtWidgets import QApplication

    apply_theme(QApplication.instance())  # type: ignore[arg-type]
    db = Database(tmp_path / "ui.db")
    ctx = build_context(db)
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    yield win
    ctx.session.shutdown()
    db.close()


def test_emergency_key_stops_running_automation(qtbot: QtBot, window: object) -> None:
    session = window._ctx.session  # type: ignore[attr-defined]
    assert session.start()
    assert session.state is EngineState.RUNNING
    press(VK_ESCAPE)
    qtbot.waitUntil(lambda: session.state is EngineState.STOPPED, timeout=3000)


def test_emergency_key_also_stops_when_paused(qtbot: QtBot, window: object) -> None:
    session = window._ctx.session  # type: ignore[attr-defined]
    assert session.start()
    session.toggle_pause()
    qtbot.waitUntil(lambda: session.state is EngineState.PAUSED, timeout=2000)
    press(VK_ESCAPE)
    qtbot.waitUntil(lambda: session.state is EngineState.STOPPED, timeout=3000)


def test_custom_emergency_key_is_used(qtbot: QtBot, window: object) -> None:
    ctx = window._ctx  # type: ignore[attr-defined]
    ctx.settings.save(AppSettings(emergency_hotkey="F11"))
    assert ctx.session.start()
    press(VK_F11)
    qtbot.waitUntil(lambda: ctx.session.state is EngineState.STOPPED, timeout=3000)


def test_hotkey_is_released_after_stop(qtbot: QtBot, window: object) -> None:
    """Depois de parar, o Esc volta a ser do usuário (outro app consegue registrá-lo)."""
    session = window._ctx.session  # type: ignore[attr-defined]
    assert session.start()
    session.stop()
    qtbot.waitUntil(lambda: session.state is EngineState.STOPPED, timeout=2000)
    qtbot.wait(50)
    user32 = ctypes.windll.user32
    assert user32.RegisterHotKey(None, 0x7777, 0x4000, VK_ESCAPE)
    user32.UnregisterHotKey(None, 0x7777)


def test_start_is_blocked_when_emergency_key_is_taken(qtbot: QtBot, window: object) -> None:
    session = window._ctx.session  # type: ignore[attr-defined]
    user32 = ctypes.windll.user32
    assert user32.RegisterHotKey(None, 0x7778, 0x4000, VK_ESCAPE), "Esc já ocupado por outro programa"
    try:
        with qtbot.waitSignal(session.start_blocked, timeout=2000) as blocker:
            assert session.start() is False
        assert "Esc" in blocker.args[0]
        assert session.state is EngineState.STOPPED
    finally:
        user32.UnregisterHotKey(None, 0x7778)


def test_settings_persist_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    first = Database(path)
    SettingsRepository(first).save(AppSettings(emergency_hotkey="Ctrl+Alt+F9"))
    first.close()
    second = Database(path)
    assert SettingsRepository(second).load().emergency_hotkey == "Ctrl+Alt+F9"
    second.close()
