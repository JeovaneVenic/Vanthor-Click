"""Partes da adaptação para Linux que não dependem do X11 (rodam em qualquer sistema)."""

from __future__ import annotations

from pathlib import Path

import pytest

from vanthor.winsys.keys import parse_combo
from vanthor.winsys.x11_keymap import vk_to_keysym_name


@pytest.mark.parametrize(
    ("text", "keysyms"),
    [
        ("Esc", ["Escape"]),
        ("Enter", ["Return"]),
        ("F5", ["F5"]),
        ("F24", ["F24"]),
        ("a", ["a"]),
        ("7", ["7"]),
        ("Ctrl+Shift+S", ["Control_L", "Shift_L", "s"]),
        ("Alt+Tab", ["Alt_L", "Tab"]),
        ("Ctrl+Down", ["Control_L", "Down"]),
        ("PgUp", ["Prior"]),
        ("Pause", ["Pause"]),
        ("Space", ["space"]),
    ],
)
def test_every_supported_key_has_x11_equivalent(text: str, keysyms: list[str]) -> None:
    combo = parse_combo(text)
    assert [vk_to_keysym_name(vk) for vk in (*combo.modifiers, combo.vk)] == keysyms


def test_all_keys_the_editor_accepts_are_mapped() -> None:
    from vanthor.winsys.keys import MODIFIER_VK, NAMED_KEYS

    for vk, _ in (*NAMED_KEYS.values(), *MODIFIER_VK.values()):
        assert vk_to_keysym_name(vk)


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        vk_to_keysym_name(0xFF)


def test_linux_data_dir_follows_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from vanthor.winsys import paths

    monkeypatch.delenv("VANTHOR_DATA_DIR", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.data_dir() == tmp_path / "xdg" / "VanthorBotClick"
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "casa")
    assert paths.data_dir() == tmp_path / "casa" / ".local" / "share" / "VanthorBotClick"


def test_windows_data_dir_is_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from vanthor.winsys import paths

    monkeypatch.delenv("VANTHOR_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert paths.data_dir() == tmp_path / "Roaming" / "VanthorBotClick"


def test_xdg_autostart_entry() -> None:
    from vanthor.winsys.autostart import xdg_autostart_entry

    entry = xdg_autostart_entry('"/home/maria/.local/lib/vanthor-bot-click/start.sh" --minimized')
    lines = entry.splitlines()
    assert lines[0] == "[Desktop Entry]"
    assert 'Exec="/home/maria/.local/lib/vanthor-bot-click/start.sh" --minimized' in lines
    assert "Type=Application" in lines and "Terminal=false" in lines
    assert entry.endswith("\n")


def test_linux_autostart_creates_and_removes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from vanthor.winsys import autostart

    monkeypatch.setattr(autostart.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("VANTHOR_LAUNCHER", "/opt/vanthor/start.sh")
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    file = tmp_path / "config" / "autostart" / "VanthorBotClick.desktop"
    assert autostart.is_enabled() and 'Exec="/opt/vanthor/start.sh" --minimized' in file.read_text("utf-8")
    autostart.set_enabled(False)
    assert not file.exists() and not autostart.is_enabled()


def test_capture_exclusion_only_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from vanthor.winsys import desktop

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    assert desktop.capture_exclusion_supported() is False
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    assert desktop.capture_exclusion_supported() is True


def test_interface_texts_name_the_system(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib
    import sys

    from vanthor.ui import copy

    try:
        monkeypatch.setattr(sys, "platform", "linux")
        assert importlib.reload(copy).SYSTEM == "sistema"
        monkeypatch.setattr(sys, "platform", "win32")
        assert importlib.reload(copy).SYSTEM == "Windows"
    finally:
        monkeypatch.undo()
        importlib.reload(copy)
