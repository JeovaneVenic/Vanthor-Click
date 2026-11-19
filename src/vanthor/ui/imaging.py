"""Conversão de imagens NumPy (RGB) para exibição no Qt."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap

from vanthor.capture.capturer import Image


def to_pixmap(image: Image, target: QSize, ratio: float) -> QPixmap:
    height, width, _ = image.shape
    contiguous = np.ascontiguousarray(image)
    qimage = QImage(contiguous.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()
    upscale = width < target.width() * ratio
    pixmap = QPixmap.fromImage(qimage).scaled(
        round(target.width() * ratio),
        round(target.height() * ratio),
        Qt.AspectRatioMode.KeepAspectRatio,
        # Zonas pequenas são ampliadas sem suavizar: o usuário vê os pixels reais que serão analisados.
        Qt.TransformationMode.FastTransformation if upscale else Qt.TransformationMode.SmoothTransformation,
    )
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def highlight_matches(image: Image, mask: NDArray[np.bool_]) -> Image:
    """Mantém as cores dos pixels encontrados e apaga o resto (cinza escuro).

    Funciona para qualquer cor procurada: pintar os acertos de verde sumiria ao procurar verde.
    """
    gray = (image.astype(np.uint16).sum(axis=2) // 3 // 5).astype(np.uint8)
    dimmed = np.repeat(gray[:, :, None], 3, axis=2)
    return np.where(mask[:, :, None], image, dimmed).astype(np.uint8)
