"""Controle segmentado: escolha única entre poucas opções, todas visíveis (sem menu escondido)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget


class Segmented(QWidget):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        """``options``: lista de (valor, rótulo)."""
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(bold)
        for value, label in options:
            button = QPushButton(label)
            button.setObjectName("Segment")
            # Largura do texto em NEGRITO (estado selecionado), senão o rótulo é cortado ao selecionar.
            button.setMinimumWidth(metrics.horizontalAdvance(label) + 28)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, v=value: self.changed.emit(v))
            self._group.addButton(button)
            layout.addWidget(button, 1)
            self._buttons[value] = button

    def value(self) -> str:
        return next(v for v, b in self._buttons.items() if b.isChecked())

    def set_value(self, value: str) -> None:
        self._buttons[value].setChecked(True)
