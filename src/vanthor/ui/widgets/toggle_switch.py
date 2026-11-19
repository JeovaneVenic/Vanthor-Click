"""Interruptor liga/desliga com movimento curto que confirma a mudança."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QAbstractButton, QWidget

from vanthor.ui.theme.tokens import Color
from vanthor.winsys.desktop import animations_enabled


class ToggleSwitch(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self, duration=140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animate = animations_enabled()
        self.toggled.connect(self._on_toggled)

    def sizeHint(self) -> QSize:
        return QSize(46, 26)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def setChecked(self, checked: bool) -> None:  # noqa: N802 (API do Qt)
        # Mudança programática (ao carregar a tela) não anima: só ações do usuário animam.
        self.blockSignals(True)
        super().setChecked(checked)
        self.blockSignals(False)
        self._anim.stop()
        self._set_offset(1.0 if checked else 0.0)

    def _on_toggled(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        if not self._animate:
            self._set_offset(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 1, self.width() - 2, self.height() - 2)
        radius = track.height() / 2

        off, on = QColor(Color.LINE_STRONG), QColor(Color.BRASA)
        color = QColor(
            round(off.red() + (on.red() - off.red()) * self._offset),
            round(off.green() + (on.green() - off.green()) * self._offset),
            round(off.blue() + (on.blue() - off.blue()) * self._offset),
        )
        if not self.isEnabled():
            color.setAlphaF(0.4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(track, radius, radius)

        knob = track.height() - 6
        x = track.left() + 3 + (track.width() - knob - 6) * self._offset
        painter.setBrush(QColor(Color.ON_BRASA if self._offset > 0.5 else Color.TEXT))
        painter.drawEllipse(QRectF(x, track.top() + 3, knob, knob))

        if self.hasFocus():
            painter.setPen(QPen(QColor(Color.TEXT), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)
        painter.end()
