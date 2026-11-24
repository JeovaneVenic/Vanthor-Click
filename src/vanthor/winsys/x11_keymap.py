"""Tradução das teclas virtuais do Windows (VK) para nomes de keysym do X11.

O app guarda teclas no formato do Windows (``winsys/keys.py``): é o que está no banco e nos perfis
exportados. No Linux, em vez de mudar esse formato (e quebrar perfis), traduzimos na hora de usar.
Módulo puro: sem X11, testável em qualquer sistema.
"""

from __future__ import annotations

_NAMED: dict[int, str] = {
    0x1B: "Escape",
    0x13: "Pause",
    0x20: "space",
    0x09: "Tab",
    0x08: "BackSpace",
    0x0D: "Return",
    0x2D: "Insert",
    0x2E: "Delete",
    0x24: "Home",
    0x23: "End",
    0x21: "Prior",  # Page Up
    0x22: "Next",  # Page Down
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x91: "Scroll_Lock",
    0x2C: "Print",
    0x11: "Control_L",
    0x12: "Alt_L",
    0x10: "Shift_L",
    0x5B: "Super_L",
}


def vk_to_keysym_name(vk: int) -> str:
    """Nome do keysym X11 para uma tecla virtual do Windows. ``ValueError`` se não houver."""
    if vk in _NAMED:
        return _NAMED[vk]
    if 0x30 <= vk <= 0x39:  # 0-9
        return chr(vk)
    if 0x41 <= vk <= 0x5A:  # A-Z: no X11 o keysym da tecla é a letra minúscula
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:  # F1-F24
        return f"F{vk - 0x70 + 1}"
    raise ValueError(f"Tecla sem equivalente no X11: {vk:#x}")
