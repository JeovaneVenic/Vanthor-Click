"""Barra lateral: marca, navegação e o estado da automação sempre visível."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from vanthor.core.engine import EngineState
from vanthor.ui.copy import STATE_COLOR, STATE_LABEL
from vanthor.ui.theme import icons
from vanthor.ui.theme.fonts import display_font
from vanthor.ui.theme.tokens import Color, Size
from vanthor.winsys.paths import resource_path


class _Dot(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(10, 10)
        self._color = Color.TEXT_3

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(1, 1, 8, 8)
        p.end()


class Sidebar(QWidget):
    page_requested = Signal(str)
    stop_requested = Signal()
    profile_clicked = Signal()

    def __init__(self, pages: list[tuple[str, str, str]], parent: QWidget | None = None) -> None:
        """``pages``: lista de (chave, rótulo, ícone)."""
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(Size.SIDEBAR_W)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 20, 16, 16)
        root.setSpacing(4)
        root.addLayout(self._brand())
        root.addSpacing(16)
        # Perfil em uso: sempre visível, porque muda tudo o que o Vanthor vigia.
        self._profile = QPushButton()
        self._profile.setObjectName("ProfileButton")
        self._profile.setCursor(Qt.CursorShape.PointingHandCursor)
        self._profile.setIcon(icons.icon("layers", Color.TEXT_2, 16))
        self._profile.setIconSize(QSize(16, 16))
        self._profile.setToolTip("Perfil em uso. Clique para gerenciar perfis.")
        self._profile.clicked.connect(self.profile_clicked.emit)
        root.addWidget(self._profile)
        root.addSpacing(12)

        self._buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label, icon_name in pages:
            button = QPushButton(f"  {label}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(icons.icon(icon_name, Color.TEXT_2, 18))
            button.setIconSize(QSize(18, 18))
            button.clicked.connect(lambda _=False, k=key: self.page_requested.emit(k))
            group.addButton(button)
            root.addWidget(button)
            self._buttons[key] = button
        root.addStretch(1)
        root.addWidget(self._dock())

    def set_profile(self, name: str) -> None:
        self._profile.setText(f"  {name}")
        self._profile.setAccessibleName(f"Perfil em uso: {name}")

    def select(self, key: str) -> None:
        self._buttons[key].setChecked(True)

    def set_state(self, state: EngineState, emergency_label: str, dry_run: bool = False) -> None:
        testing = dry_run and state is EngineState.RUNNING
        self._dot.set_color(Color.SIM if testing else STATE_COLOR[state])
        self._state_label.setText("Modo de teste" if testing else STATE_LABEL[state])
        active = state is not EngineState.STOPPED
        self._hint.setText(
            f"{emergency_label} para tudo na hora" if active else f"Tecla de emergência: {emergency_label}"
        )
        self._stop.setVisible(active)

    def _brand(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        mark = QLabel()
        pm = QPixmap(str(resource_path("brand", "mark.png")))
        ratio = self.devicePixelRatioF()
        pm = pm.scaled(
            round(40 * ratio), round(40 * ratio), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pm.setDevicePixelRatio(ratio)
        mark.setPixmap(pm)
        mark.setFixedSize(40, 40)
        row.addWidget(mark)

        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel("VANTHOR")
        name.setObjectName("BrandName")
        name.setFont(display_font(17, QFont.Weight.DemiBold))
        product = QLabel("BOT CLICK")
        product.setObjectName("BrandProduct")
        product.setFont(display_font(12, QFont.Weight.Normal))
        product_font = product.font()
        product_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        product.setFont(product_font)
        names.addWidget(name)
        names.addWidget(product)
        row.addLayout(names)
        row.addStretch(1)
        return row

    def _dock(self) -> QWidget:
        dock = QWidget()
        dock.setObjectName("Dock")
        dock.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(dock)
        layout.setContentsMargins(4, 14, 4, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._dot = _Dot()
        self._state_label = QLabel()
        self._state_label.setObjectName("DockState")
        row.addWidget(self._dot)
        row.addWidget(self._state_label)
        row.addStretch(1)
        layout.addLayout(row)

        self._hint = QLabel()
        self._hint.setObjectName("DockHint")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._stop = QPushButton("  Parar tudo")
        self._stop.setIcon(icons.icon("square", Color.TEXT, 16))
        self._stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop.clicked.connect(self.stop_requested.emit)
        self._stop.setVisible(False)
        layout.addSpacing(4)
        layout.addWidget(self._stop)
        return dock
