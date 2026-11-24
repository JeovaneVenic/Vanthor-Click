"""Mouse e teclado nativos do Windows via SendInput.

Por que não PyAutoGUI: ele é lento, tem um "fail-safe" de canto que não controlamos e erra
coordenadas com escala de DPI. Aqui o processo é Per-Monitor DPI Aware, então SetCursorPos
recebe pixels físicos exatos — os mesmos da captura.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from vanthor.actions.models import MouseButton
from vanthor.winsys.keys import EXTENDED_VK

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_MAPVK_VK_TO_VSC = 0

_BUTTON_FLAGS: dict[MouseButton, tuple[int, int]] = {
    MouseButton.LEFT: (0x0002, 0x0004),
    MouseButton.RIGHT: (0x0008, 0x0010),
    MouseButton.MIDDLE: (0x0020, 0x0040),
}


class InputError(RuntimeError):
    """O Windows recusou a entrada simulada."""


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class Win32Input:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Entrada nativa só existe no Windows")
        self._user32 = ctypes.windll.user32
        self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
        self._user32.SendInput.restype = wintypes.UINT

    def get_cursor(self) -> tuple[int, int]:
        point = wintypes.POINT()
        if not self._user32.GetCursorPos(ctypes.byref(point)):
            raise InputError("Não foi possível ler a posição do mouse")
        return point.x, point.y

    def set_cursor(self, x: int, y: int) -> None:
        if not self._user32.SetCursorPos(x, y):
            raise InputError(f"O Windows não moveu o mouse para {x}, {y}")

    def mouse(self, button: MouseButton, down: bool) -> None:
        flags = _BUTTON_FLAGS[button][0 if down else 1]
        event = _INPUT(type=_INPUT_MOUSE)
        event.u.mi = _MOUSEINPUT(0, 0, 0, flags, 0, 0)
        self._send(event)

    def key(self, vk: int, down: bool) -> None:
        flags = 0 if down else _KEYEVENTF_KEYUP
        if vk in EXTENDED_VK:
            flags |= _KEYEVENTF_EXTENDEDKEY
        scan = self._user32.MapVirtualKeyW(vk, _MAPVK_VK_TO_VSC)
        event = _INPUT(type=_INPUT_KEYBOARD)
        event.u.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
        self._send(event)

    def _send(self, event: _INPUT) -> None:
        if self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)) != 1:
            # Causa mais comum: a janela de destino roda como administrador e o Vanthor não (UIPI).
            raise InputError(
                "O Windows bloqueou o clique ou a tecla. Se o programa de destino roda como "
                "administrador, abra o Vanthor como administrador também."
            )


def create_input_backend() -> Win32Input:
    """Mouse e teclado do sistema atual: Windows (SendInput) ou Linux X11 (XTest), mesma interface."""
    if sys.platform.startswith("linux"):
        from vanthor.winsys.x11 import X11Error, X11Input

        try:
            return X11Input()  # type: ignore[return-value]
        except X11Error as exc:
            raise InputError(str(exc)) from exc
    return Win32Input()
