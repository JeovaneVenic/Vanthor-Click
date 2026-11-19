"""Modo de teste: um alvo ciano aparece por um instante onde o clique ACONTECERIA."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from vanthor.capture.geometry import monitor_for_point
from vanthor.ui.screens import monitor_maps
from vanthor.ui.theme.fonts import ui_font
from vanthor.ui.theme.tokens import Color
from vanthor.winsys.desktop import animations_enabled, exclude_from_capture

_SIZE = 240  # janela lógica: alvo ao centro e rótulo abaixo
_DURATION_MS = 1100
_MAX_ON_SCREEN = 12  # muitos cliques simulados seguidos não podem encher a tela de janelas


class ClickFlashes:
    def __init__(self) -> None:
        self._active: list[_Flash] = []

    def flash(self, x: int, y: int, label: str) -> None:
        monitor = monitor_for_point(x, y, monitor_maps())
        if monitor is None:
            return
        while len(self._active) >= _MAX_ON_SCREEN:
            self._active.pop(0).close()
        lx, ly = monitor.to_logical(x, y)
        flash = _Flash(label, self._active.remove)
        flash.setGeometry(round(lx - _SIZE / 2), round(ly - _SIZE / 2), _SIZE, _SIZE)
        flash.show()
        exclude_from_capture(int(flash.winId()))
        self._active.append(flash)

    def clear(self) -> None:
        for flash in list(self._active):
            flash.close()
        self._active.clear()


class _Flash(QWidget):
    def __init__(self, label: str, on_done: object) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._label = label
        self._progress = 0.0
        self._on_done = on_done
        if animations_enabled():
            self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=_DURATION_MS)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.valueChanged.connect(self._step)
            self._anim.finished.connect(self._finish)
            self._anim.start()
        else:
            QTimer.singleShot(_DURATION_MS, self._finish)

    def _step(self, value: object) -> None:
        if isinstance(value, float):
            self._progress = value
            self.update()

    def _finish(self) -> None:
        if callable(self._on_done):
            try:
                self._on_done(self)
            except ValueError:
                pass
        self.close()
        self.deleteLater()

    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(_SIZE / 2, _SIZE / 2)
        fade = 1.0 - max(0.0, self._progress - 0.6) / 0.4  # some nos últimos 40%
        ring = QColor(Color.SIM)
        ring.setAlphaF(0.9 * fade)
        radius = 10 + 26 * self._progress
        p.setPen(QPen(ring, 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, radius, radius)
        p.setPen(QPen(ring, 2))
        p.drawLine(QPointF(c.x() - 12, c.y()), QPointF(c.x() + 12, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 12), QPointF(c.x(), c.y() + 12))

        p.setFont(ui_font(12, QFont.Weight.DemiBold))
        metrics = p.fontMetrics()
        text = metrics.elidedText(self._label, Qt.TextElideMode.ElideRight, _SIZE - 16)
        w = metrics.horizontalAdvance(text) + 16
        tag = QRectF((_SIZE - w) / 2, c.y() + 44, w, 22)
        bg = QColor(Color.SIM)
        bg.setAlphaF(fade)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(tag, 6, 6)
        fg = QColor(Color.BG)
        fg.setAlphaF(fade)
        p.setPen(fg)
        p.drawText(tag, Qt.AlignmentFlag.AlignCenter, text)
        p.end()
