"""Aviso temporário com ação (ex.: "Zona 02 excluída · Desfazer").

Usado no lugar de diálogos "Tem certeza?": a ação acontece na hora e pode ser desfeita por 5 s.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from vanthor.ui.theme.tokens import Color


class Toast(QFrame):
    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("Toast")
        self.setStyleSheet(
            f"#Toast {{ background: {Color.RAISED}; border: 1px solid {Color.LINE_STRONG}; border-radius: 12px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 8, 8, 8)
        layout.setSpacing(14)
        self._text = QLabel()
        layout.addWidget(self._text)
        self._action = QPushButton()
        self._action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action.clicked.connect(self._on_action)
        layout.addWidget(self._action)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._expire)
        self._on_undo: Callable[[], None] | None = None
        self._on_expire: Callable[[], None] | None = None
        host.installEventFilter(self)
        self.hide()

    def show_message(
        self,
        text: str,
        action: str,
        on_action: Callable[[], None],
        on_expire: Callable[[], None],
        seconds: float = 5.0,
    ) -> None:
        self.flush()  # um aviso novo confirma o anterior
        self._text.setText(text)
        self._action.setText(action)
        self._on_undo, self._on_expire = on_action, on_expire
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(int(seconds * 1000))

    def flush(self) -> None:
        """Confirma imediatamente a ação pendente (ex.: ao fechar o aplicativo)."""
        if self._timer.isActive():
            self._timer.stop()
            self._expire()

    def _on_action(self) -> None:
        self._timer.stop()
        callback, self._on_undo, self._on_expire = self._on_undo, None, None
        self.hide()
        if callback:
            callback()

    def _expire(self) -> None:
        callback, self._on_undo, self._on_expire = self._on_expire, None, None
        self.hide()
        if callback:
            callback()

    def _place(self) -> None:
        host = self.parentWidget()
        if host is not None:
            self.move((host.width() - self.width()) // 2, host.height() - self.height() - 24)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
