"""Rótulo com quebra de linha que sempre tem a altura certa.

O QLabel com ``wordWrap`` dentro de áreas de rolagem calcula a altura antes de saber a largura
final e acaba cortando o texto. Este rótulo recalcula a altura mínima a cada texto e largura novos.
"""

from __future__ import annotations

from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLabel, QWidget


class WrapLabel(QLabel):
    def __init__(self, text: str = "", object_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        if object_name:
            self.setObjectName(object_name)
        self.setWordWrap(True)

    def setText(self, text: str) -> None:  # noqa: N802 (API do Qt)
        super().setText(text)
        self._fit()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        width = self.width() if self.width() > 50 else 600
        self.setMinimumHeight(self.heightForWidth(width))
