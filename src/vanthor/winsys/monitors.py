"""Monitores físicos via Win32 (retângulos em pixels reais, DPI por monitor).

Requer processo "Per-Monitor DPI Aware v2" (o Qt 6 ativa isso ao criar a QApplication; sem isso
o Windows devolveria coordenadas virtualizadas e as zonas sairiam deslocadas).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

from vanthor.capture.geometry import ScreenRect

_MONITORINFOF_PRIMARY = 0x1
_MDT_EFFECTIVE_DPI = 0
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


@dataclass(frozen=True, slots=True)
class PhysicalMonitor:
    name: str
    rect: ScreenRect
    primary: bool
    scale: float


def enumerate_monitors() -> list[PhysicalMonitor]:
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    shcore = ctypes.windll.shcore
    found: list[PhysicalMonitor] = []

    monitor_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def callback(hmonitor: int, _hdc: int, _rect: object, _data: int) -> bool:
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            return True
        r = info.rcMonitor
        dpi_x, dpi_y = wintypes.UINT(96), wintypes.UINT(96)
        shcore.GetDpiForMonitor(hmonitor, _MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        found.append(
            PhysicalMonitor(
                name=info.szDevice,
                rect=ScreenRect(r.left, r.top, r.right - r.left, r.bottom - r.top),
                primary=bool(info.dwFlags & _MONITORINFOF_PRIMARY),
                scale=dpi_x.value / 96,
            )
        )
        return True

    if not user32.EnumDisplayMonitors(None, None, monitor_enum_proc(callback), 0):
        raise OSError("O Windows não listou os monitores")
    # Ordem estável: principal primeiro, depois da esquerda para a direita.
    found.sort(key=lambda m: (not m.primary, m.rect.x, m.rect.y))
    return found


def ensure_dpi_awareness() -> None:
    """Para processos sem Qt (testes, ferramentas): ativa Per-Monitor v2 se ainda não estiver."""
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    # Falha silenciosa é esperada quando já foi definido (o Qt também define): não há o que fazer.
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
