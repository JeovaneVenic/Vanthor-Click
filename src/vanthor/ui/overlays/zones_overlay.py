"""Mostra as zonas por cima de tudo, sem atrapalhar.

Garantias pedidas no projeto:
- Transparente para o mouse (WindowTransparentForInput): cliques atravessam.
- Invisível para a captura (WDA_EXCLUDEFROMCAPTURE): a moldura colorida não altera a detecção.
- Nunca recebe foco: não rouba o teclado do programa em uso.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from vanthor.capture.geometry import MonitorMap
from vanthor.capture.zones import Zone
from vanthor.ui.screens import monitor_maps, screen_for_map
from vanthor.ui.theme.fonts import ui_font
from vanthor.ui.theme.tokens import Color
from vanthor.winsys.desktop import exclude_from_capture

ClickPoint = tuple[int, int, str]  # x, y físicos e rótulo


class ZonesOverlay:
    def __init__(self) -> None:
        self._windows: list[_OverlayWindow] = []
        self._zones: list[Zone] = []
        self._points: list[ClickPoint] = []

    @property
    def visible(self) -> bool:
        return bool(self._windows)

    def show(self, zones: list[Zone], points: list[ClickPoint] | None = None) -> None:
        self.hide()
        self._zones = zones
        self._points = points or []
        for monitor in monitor_maps():
            screen = screen_for_map(monitor)
            if screen is None:
                continue
            window = _OverlayWindow(monitor)
            window.setGeometry(screen.geometry())
            window.set_zones(zones, self._points)
            window.show()
            exclude_from_capture(int(window.winId()))
            self._windows.append(window)

    def update_zones(self, zones: list[Zone], points: list[ClickPoint] | None = None) -> None:
        self._zones = zones
        if points is not None:
            self._points = points
        for window in self._windows:
            window.set_zones(zones, self._points)

    def hide(self) -> None:
        for window in self._windows:
            window.close()
            window.deleteLater()
        self._windows.clear()


class _OverlayWindow(QWidget):
    def __init__(self, monitor: MonitorMap) -> None:
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
        self._monitor = monitor
        self._zones: list[Zone] = []
        self._points: list[ClickPoint] = []

    def set_zones(self, zones: list[Zone], points: list[ClickPoint]) -> None:
        self._zones = [z for z in zones if self._monitor.physical.contains_rect(z.rect)]
        self._points = [p for p in points if self._monitor.physical.contains_point(p[0], p[1])]
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        origin = self.geometry().topLeft()
        font = ui_font(13, QFont.Weight.DemiBold)
        p.setFont(font)
        metrics = p.fontMetrics()
        for zone in self._zones:
            x, y, w, h = self._monitor.rect_to_logical(zone.rect)
            r = QRectF(x - origin.x(), y - origin.y(), w, h)
            color = QColor(zone.color)
            if not zone.enabled:
                color.setAlpha(110)
            fill = QColor(color)
            fill.setAlpha(26)
            p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine if zone.enabled else Qt.PenStyle.DashLine))
            p.setBrush(fill)
            p.drawRect(r.adjusted(-1, -1, 1, 1))

            label = zone.name if zone.enabled else f"{zone.name} (desligada)"
            lw, lh = metrics.horizontalAdvance(label) + 14, metrics.height() + 6
            ly = r.top() - lh - 4 if r.top() - lh - 4 >= 0 else r.bottom() + 4
            tag = QRectF(r.left() - 1, ly, lw, lh)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(tag, 5, 5)
            p.setPen(QColor(Color.BG))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)

        # Pontos de clique: alvo em brasa, para ligar "onde observa" a "onde clica".
        brasa = QColor(Color.BRASA)
        for x_phys, y_phys, label in self._points:
            lx, ly = self._monitor.to_logical(x_phys, y_phys)
            c = QPointF(lx - origin.x(), ly - origin.y())
            p.setPen(QPen(brasa, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 8, 8)
            p.drawLine(QPointF(c.x() - 13, c.y()), QPointF(c.x() + 13, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - 13), QPointF(c.x(), c.y() + 13))
            lw, lh = metrics.horizontalAdvance(label) + 14, metrics.height() + 6
            tag = QRectF(c.x() + 14, c.y() - lh - 6, lw, lh)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(brasa)
            p.drawRoundedRect(tag, 5, 5)
            p.setPen(QColor(Color.ON_BRASA))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)
        p.end()
