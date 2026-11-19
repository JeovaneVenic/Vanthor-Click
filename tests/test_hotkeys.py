from __future__ import annotations

import pytest

from vanthor.winsys.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, HotkeyError, parse_hotkey


@pytest.mark.parametrize(
    ("text", "modifiers", "vk", "label"),
    [
        ("Esc", 0, 0x1B, "Esc"),
        ("Escape", 0, 0x1B, "Esc"),
        ("Pause", 0, 0x13, "Pause"),
        ("F12", 0, 0x7B, "F12"),
        ("F24", 0, 0x87, "F24"),
        ("Ctrl+Shift+F12", MOD_CONTROL | MOD_SHIFT, 0x7B, "Ctrl+Shift+F12"),
        ("Alt+Q", MOD_ALT, ord("Q"), "Alt+Q"),
        ("ctrl+alt+1", MOD_CONTROL | MOD_ALT, ord("1"), "Ctrl+Alt+1"),
    ],
)
def test_parse_valid_hotkeys(text: str, modifiers: int, vk: int, label: str) -> None:
    hotkey = parse_hotkey(text)
    assert (hotkey.modifiers, hotkey.vk, hotkey.label) == (modifiers, vk, label)


@pytest.mark.parametrize("text", ["", "Ctrl+", "+A", "Hyper+A", "Ctrl+Ç", "F25", "Ctrl+Shift"])
def test_reject_malformed_hotkeys(text: str) -> None:
    with pytest.raises(HotkeyError):
        parse_hotkey(text)


@pytest.mark.parametrize("text", ["A", "1", "Space", "Enter"])
def test_reject_bare_typing_keys_as_global_hotkey(text: str) -> None:
    # Um atalho global sem modificador nessas teclas travaria a digitação no Windows inteiro.
    with pytest.raises(HotkeyError, match="combine"):
        parse_hotkey(text)
