"""O "olho da pantera": indicador de estado lido de relance.

Parado: olho apagado, cinza. Vigiando: íris em brasa com um brilho que respira devagar.
Pausado: íris aço, sem brilho. O texto ao lado sempre repete o estado (não dependemos só de cor).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPaintEvent, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from vanthor.core.engine import EngineState
from vanthor.ui.theme.tokens import Color
from vanthor.winsys.desktop import animations_enabled


class StatusEye(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = EngineState.STOPPED
        self._testing = False
        self._breath = 0.6
        self._animate = animations_enabled()
        # Uma volta completa (inspira e expira) a cada 4,8 s: perceptível sem chamar atenção.
        self._pulse = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=4800)
        self._pulse.setEasingCurve(QEasingCurve.Type.Linear)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._on_breath)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Indicador de estado")

    def sizeHint(self) -> QSize:
        return QSize(132, 84)

    def set_state(self, state: EngineState, testing: bool = False) -> None:
        """``testing``: modo de teste. A íris fica ciano (testando) em vez de brasa (agindo de verdade)."""
        self._state = state
        self._testing = testing and state is EngineState.RUNNING
        self.setAccessibleDescription(
            "Modo de teste"
            if self._testing
            else {EngineState.STOPPED: "Parado", EngineState.RUNNING: "Vigiando", EngineState.PAUSED: "Pausado"}[state]
        )
        if state is EngineState.RUNNING and self._animate:
            if self._pulse.state() != QVariantAnimation.State.Running:
                self._pulse.start()
        else:
            self._pulse.stop()
            self._breath = 0.6
        self.update()

    def _on_breath(self, value: object) -> None:
        if isinstance(value, float):
            # Cosseno: sobe e desce sem degrau na emenda entre voltas.
            self._breath = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(2 * math.pi * value))
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = 104.0, 52.0
        left = (self.width() - w) / 2
        top = (self.height() - h) / 2
        eye = self._eye_path(QRectF(left, top, w, h))
        center = QPointF(left + w * 0.5, top + h * 0.5)
        iris_r = h * 0.44

        running = self._state is EngineState.RUNNING
        paused = self._state is EngineState.PAUSED
        accent = Color.SIM if self._testing else Color.BRASA

        if running:
            # Brilho elíptico que se apaga antes da borda do widget (sem cantos cortados).
            radius = self.height() / 2
            glow = QRadialGradient(QPointF(0, 0), radius)
            glow_color = QColor(accent)
            glow_color.setAlphaF(0.32 * self._breath)
            glow.setColorAt(0.0, glow_color)
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.save()
            painter.translate(center)
            painter.scale(self.width() / self.height(), 1.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(QPointF(0, 0), radius, radius)
            painter.restore()

        painter.fillPath(eye, QColor("#0C0D0F"))

        painter.save()
        painter.setClipPath(eye)
        iris = QRadialGradient(center - QPointF(0, iris_r * 0.25), iris_r * 1.15)
        if running and self._testing:
            iris.setColorAt(0.0, QColor("#CFFAFE"))
            iris.setColorAt(0.45, QColor(Color.SIM))
            iris.setColorAt(1.0, QColor("#0E4F5C"))
        elif running:
            iris.setColorAt(0.0, QColor("#FFC28A"))
            iris.setColorAt(0.45, QColor(Color.BRASA))
            iris.setColorAt(1.0, QColor("#6E2400"))
        elif paused:
            iris.setColorAt(0.0, QColor("#D5DAE1"))
            iris.setColorAt(0.6, QColor(Color.STEEL))
            iris.setColorAt(1.0, QColor("#4A5058"))
        else:
            iris.setColorAt(0.0, QColor("#3A3F46"))
            iris.setColorAt(1.0, QColor("#202328"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(iris))
        painter.drawEllipse(center, iris_r, iris_r)

        # Pupila em fenda, como a de um felino.
        slit_w = iris_r * (0.22 if running else 0.32)
        painter.setBrush(QColor("#08090A"))
        painter.drawEllipse(center, slit_w, iris_r * 0.86)
        if running or paused:
            painter.setBrush(QColor(255, 255, 255, 150))
            painter.drawEllipse(center + QPointF(-iris_r * 0.38, -iris_r * 0.4), 2.4, 2.4)
        painter.restore()

        outline = QColor(accent if running else Color.LINE_STRONG)
        if running:
            outline.setAlphaF(0.55 + 0.45 * self._breath)
        painter.setPen(QPen(outline, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(eye)
        painter.end()

    @staticmethod
    def _eye_path(r: QRectF) -> QPainterPath:
        # Amêndoa com o canto externo levemente elevado: o olhar atento da logo.
        path = QPainterPath(QPointF(r.left(), r.center().y() + r.height() * 0.06))
        path.cubicTo(
            QPointF(r.left() + r.width() * 0.22, r.top() - r.height() * 0.05),
            QPointF(r.right() - r.width() * 0.24, r.top() - r.height() * 0.08),
            QPointF(r.right(), r.center().y() - r.height() * 0.14),
        )
        path.cubicTo(
            QPointF(r.right() - r.width() * 0.2, r.bottom() + r.height() * 0.02),
            QPointF(r.left() + r.width() * 0.26, r.bottom() + r.height() * 0.06),
            QPointF(r.left(), r.center().y() + r.height() * 0.06),
        )
        return path
