"""Atalho global de emergência via RegisterHotKey.

Usamos RegisterHotKey (API oficial) em vez de um gancho de teclado de baixo nível: o gancho
"enxerga" todas as teclas e por isso é tratado como suspeito por antivírus.
O atalho só fica registrado enquanto há automação ativa, porque um Esc global rouba o Esc
de todos os outros programas.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from dataclasses import dataclass

from vanthor.winsys.keys import KeyCombo, KeyError_, parse_key

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

_MODIFIERS: dict[str, tuple[int, str]] = {
    "ctrl": (MOD_CONTROL, "Ctrl"),
    "control": (MOD_CONTROL, "Ctrl"),
    "alt": (MOD_ALT, "Alt"),
    "shift": (MOD_SHIFT, "Shift"),
    "meta": (MOD_WIN, "Win"),
    "win": (MOD_WIN, "Win"),
}


class HotkeyError(ValueError):
    """Texto de atalho inválido."""


@dataclass(frozen=True, slots=True)
class Hotkey:
    modifiers: int
    vk: int
    label: str


def parse_hotkey(text: str) -> Hotkey:
    """Converte "Esc", "Ctrl+Shift+F12" ou "Alt+Q" no par (modificadores, tecla virtual)."""
    parts = [p.strip() for p in text.split("+")]
    if not text.strip() or any(not p for p in parts):
        raise HotkeyError("Atalho vazio ou malformado")

    modifiers = 0
    labels: list[str] = []
    for part in parts[:-1]:
        entry = _MODIFIERS.get(part.lower())
        if entry is None:
            raise HotkeyError(f"Modificador desconhecido: {part}")
        modifiers |= entry[0]
        labels.append(entry[1])

    try:
        vk, key_label = parse_key(parts[-1])
    except KeyError_ as exc:
        raise HotkeyError(str(exc)) from exc
    if modifiers == 0 and vk not in (0x1B, 0x13) and not 0x70 <= vk <= 0x87:
        # Uma letra ou número sozinho como atalho global quebraria a digitação em
        # todo o Windows enquanto a automação roda.
        raise HotkeyError("Use Esc, Pause, uma tecla F ou combine com Ctrl/Alt/Shift")
    return Hotkey(modifiers, vk, "+".join([*labels, key_label]))


_VK_TO_MOD = {0x11: MOD_CONTROL, 0x12: MOD_ALT, 0x10: MOD_SHIFT, 0x5B: MOD_WIN}


def taken_by_another_program(combo: KeyCombo) -> bool:
    """Outro programa registrou esta combinação como atalho global?

    Se sim, o Windows entrega a tecla a ele, e uma ação "pressionar teclas" nunca chega ao destino.
    Testamos tentando registrar e liberando em seguida.
    """
    if combo.is_bare:
        return False
    if sys.platform.startswith("linux"):
        from vanthor.winsys.x11 import combo_taken

        return combo_taken(combo.modifiers, combo.vk)
    if sys.platform != "win32":
        return False
    modifiers = 0
    for vk in combo.modifiers:
        modifiers |= _VK_TO_MOD.get(vk, 0)
    user32 = ctypes.windll.user32
    probe_id = 0xB0C2
    if user32.RegisterHotKey(None, probe_id, modifiers | MOD_NOREPEAT, combo.vk):
        user32.UnregisterHotKey(None, probe_id)
        return False
    return True


class GlobalHotkey:
    """Registra um único atalho associado a uma janela (recebe WM_HOTKEY no nativeEvent)."""

    def __init__(self, hotkey_id: int = 0xB0C1) -> None:
        self._id = hotkey_id
        self._hwnd: int | None = None

    @property
    def hotkey_id(self) -> int:
        return self._id

    @property
    def registered(self) -> bool:
        return self._hwnd is not None

    def register(self, hwnd: int, hotkey: Hotkey) -> bool:
        self.unregister()
        if sys.platform != "win32":
            return False
        ok = bool(ctypes.windll.user32.RegisterHotKey(hwnd, self._id, hotkey.modifiers | MOD_NOREPEAT, hotkey.vk))
        if ok:
            self._hwnd = hwnd
        return ok

    def unregister(self) -> None:
        if self._hwnd is not None and sys.platform == "win32":
            ctypes.windll.user32.UnregisterHotKey(self._hwnd, self._id)
        self._hwnd = None


def create_global_hotkey(on_trigger: Callable[[], None]) -> GlobalHotkey:
    """Tecla de emergência do sistema atual.

    Windows: ``RegisterHotKey`` (o aviso chega pela janela, em ``nativeEvent``; ``on_trigger`` não é usado).
    Linux X11: ``XGrabKey`` com uma thread própria que chama ``on_trigger`` ao pressionar.
    """
    if sys.platform.startswith("linux"):
        from vanthor.winsys.x11 import X11GlobalHotkey

        return X11GlobalHotkey(on_trigger)  # type: ignore[return-value]
    return GlobalHotkey()
