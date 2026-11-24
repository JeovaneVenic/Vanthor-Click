"""Pequenas integrações visuais com o Windows."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_SPI_GETCLIENTAREAANIMATION = 0x1042


def _colorref(hex_color: str) -> wintypes.DWORD:
    value = hex_color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return wintypes.DWORD(r | (g << 8) | (b << 16))


def style_title_bar(hwnd: int, caption_hex: str, border_hex: str) -> None:
    """Barra de título escura e da cor do fundo; no Windows 10 só o modo escuro se aplica."""
    if sys.platform != "win32":
        return
    dwm = ctypes.windll.dwmapi
    dark = wintypes.BOOL(True)
    dwm.DwmSetWindowAttribute(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark), ctypes.sizeof(dark))
    for attribute, color in ((_DWMWA_CAPTION_COLOR, caption_hex), (_DWMWA_BORDER_COLOR, border_hex)):
        ref = _colorref(color)
        dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(ref), ctypes.sizeof(ref))


def animations_enabled() -> bool:
    """Respeita "Efeitos de animação" do Windows (acessibilidade)."""
    if sys.platform != "win32":
        return True
    enabled = wintypes.BOOL(True)
    if not ctypes.windll.user32.SystemParametersInfoW(_SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0):
        log.warning("Não foi possível ler a preferência de animações do Windows")
        return True
    return bool(enabled.value)


_WDA_EXCLUDEFROMCAPTURE = 0x11


def capture_exclusion_supported() -> bool:
    """O sistema consegue esconder janelas das capturas de tela?

    Windows 10 2004+: sim. Linux X11: não existe equivalente; as sobreposições se adaptam
    (desenho por fora das zonas, conta-gotas sobre uma foto da tela tirada antes de abrir).
    """
    return sys.platform == "win32"


def exclude_from_capture(hwnd: int) -> bool:
    """Deixa a janela invisível para capturas de tela (inclusive as nossas).

    Essencial para as sobreposições: a borda colorida de uma zona não pode ser "vista" pela
    detecção de cor daquela mesma zona. Requer Windows 10 2004 ou mais novo.
    """
    if sys.platform != "win32":
        return False
    ok = bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE))
    if not ok:
        log.warning("SetWindowDisplayAffinity falhou (Windows anterior ao 10 2004?)")
    return ok


APP_MUTEX_NAME = "VanthorBotClickRunning"  # o instalador (AppMutex) usa o mesmo nome


def create_app_mutex() -> int | None:
    """Mutex nomeado enquanto o app roda: o instalador detecta e pede para fechá-lo antes de atualizar.

    Guarde o retorno durante toda a vida do processo (o Windows libera ao encerrar).
    """
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
    return int(handle) if handle else None


def set_app_user_model_id(app_id: str) -> None:
    """Faz o Windows agrupar a janela com o ícone do app na barra de tarefas (e não do Python)."""
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
