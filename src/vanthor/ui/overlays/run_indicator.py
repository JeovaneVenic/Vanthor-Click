"""Indicador permanente de execução: uma pílula sempre visível enquanto há automação.

Requisito de segurança: mesmo com o Vanthor minimizado, o usuário sabe que algo está vigiando e
tem um botão "Parar" à mão. A pílula não rouba o foco, pode ser arrastada para longe de uma zona
e é invisível para a captura (não interfere na detecção).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from vanthor.core.engine import EngineState
from vanthor.ui.theme.tokens import Color
from vanthor.winsys.desktop import exclude_from_capture


class _Dot(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(12, 12)
        self.color = Color.BRASA

    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(1, 1, 10, 10)
        p.end()


class RunIndicator(QWidget):
    stop_requested = Signal()
    open_requested = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAccessibleName("Indicador de automação em execução")
        self._accent = Color.BRASA
        self._drag_from: QPoint | None = None
        self._placed = False

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 8, 8)
        row.setSpacing(10)
        self._dot = _Dot()
        row.addWidget(self._dot)
        self._text = QLabel()
        self._text.setStyleSheet("font-weight: 600; background: transparent;")
        row.addWidget(self._text)
        self._hint = QLabel()
        self._hint.setStyleSheet(f"color: {Color.TEXT_2}; background: transparent;")
        row.addWidget(self._hint)
        stop = QPushButton("Parar")
        stop.setCursor(Qt.CursorShape.PointingHandCursor)
        stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        stop.setToolTip("Para todas as automações")
        stop.setStyleSheet(
            f"QPushButton {{ min-height: 30px; padding: 0 14px; border-radius: 15px; border: none;"
            f" background: {Color.TEXT}; color: {Color.BG}; font-weight: 600; }}"
            "QPushButton:hover { background: #FFFFFF; }"
        )
        stop.clicked.connect(self.stop_requested.emit)
        row.addWidget(stop)

    def update_state(self, state: EngineState, dry_run: bool, rules: int, emergency: str) -> None:
        if state is EngineState.PAUSED:
            self._accent, text = Color.STEEL, "Pausado"
        elif dry_run:
            self._accent, text = Color.SIM, f"Modo de teste: {rules} {'automação' if rules == 1 else 'automações'}"
        else:
            self._accent, text = Color.BRASA, f"Vigiando {rules} {'automação' if rules == 1 else 'automações'}"
        self._dot.color = self._accent
        self._text.setText(text)
        self._hint.setText(f"{emergency} para tudo")
        self.adjustSize()
        self.update()

    def show_running(self) -> None:
        if not self._placed:
            screen = QGuiApplication.primaryScreen().availableGeometry()
            self.adjustSize()
            self.move(screen.center().x() - self.width() // 2, screen.top() + 12)
            self._placed = True
        if not self.isVisible():
            self.show()
            exclude_from_capture(int(self.winId()))

    # -- arrastar para tirar da frente --------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._drag_from = None

    def mouseDoubleClickEvent(self, _event: QMouseEvent) -> None:
        self.open_requested.emit()

    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(self._accent), 1.5))
        p.setBrush(QColor(Color.SURFACE))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.end()
