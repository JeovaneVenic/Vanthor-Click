"""Captura de um retângulo da tela (só a área pedida, nunca a tela inteira)."""

from __future__ import annotations

import threading

import mss
import numpy as np
from numpy.typing import NDArray

from vanthor.capture.geometry import ScreenRect

Image = NDArray[np.uint8]  # (altura, largura, 3) em RGB


class CaptureError(RuntimeError):
    """A captura falhou (monitor desligado, sessão bloqueada, tela segura do Windows...)."""


class RegionCapturer:
    """Capturador ligado a UMA thread: o MSS guarda contextos GDI por thread.

    Crie e use na mesma thread. ``close`` pode ser chamado de qualquer thread.
    """

    def __init__(self) -> None:
        self._owner = threading.get_ident()
        self._sct: mss.MSS | None = None

    def grab(self, rect: ScreenRect) -> Image:
        if threading.get_ident() != self._owner:
            raise RuntimeError("RegionCapturer usado fora da thread que o criou")
        if self._sct is None:
            self._sct = mss.MSS()
        try:
            shot = self._sct.grab({"left": rect.x, "top": rect.y, "width": rect.width, "height": rect.height})
        except mss.ScreenShotError as exc:
            raise CaptureError(str(exc)) from exc
        bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        # BGRA → RGB contíguo (o detector da Fase 3 trabalha em RGB).
        return np.ascontiguousarray(bgra[:, :, 2::-1])

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None
