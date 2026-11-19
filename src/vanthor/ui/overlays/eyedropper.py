"""Conta-gotas: escolher uma cor clicando em qualquer ponto da tela.

A lupa mostra os pixels REAIS ao redor do cursor (capturados ao vivo), ampliados, com o pixel
central marcado. A janela é invisível para a captura, então a lupa nunca enxerga a si mesma.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from vanthor.capture.capturer import CaptureError, RegionCapturer
from vanthor.capture.geometry import MonitorMap, ScreenRect
from vanthor.ui.overlays.host import modal_host
from vanthor.ui.screens import monitor_maps, screen_for_map
from vanthor.ui.theme.fonts import display_font, ui_font
from vanthor.ui.theme.tokens import Color
from vanthor.vision.color import RGB, rgb_to_hex
from vanthor.winsys.desktop import exclude_from_capture

_RADIUS = 7  # pixels físicos de cada lado do central (lupa de 15×15)
_ZOOM = 10


@dataclass(frozen=True, slots=True)
class Marker:
    """Referência desenhada na tela durante a escolha (ex.: a zona observada, pontos já escolhidos)."""

    rect: ScreenRect
    color: str
    label: str


class Eyedropper(QObject):
    """Escolhe um pixel na tela. Serve ao conta-gotas (usa a cor) e ao seletor de ponto (usa x, y)."""

    picked = Signal(object, int, int)  # RGB, x físico, y físico
    cancelled = Signal()

    def __init__(
        self,
        title: str = "Conta-gotas",
        hint: str = "Clique na cor que o Vanthor deve procurar. Esc cancela.",
        markers: tuple[Marker, ...] = (),
    ) -> None:
        super().__init__()
        self.title, self.hint, self.markers = title, hint, markers
        self._overlays: list[_EyedropperOverlay] = []
        self._capturer = RegionCapturer()
        self._done = False

    def start(self) -> None:
        for monitor in monitor_maps():
            screen = screen_for_map(monitor)
            if screen is None:
                continue
            overlay = _EyedropperOverlay(self, monitor)
            overlay.setGeometry(screen.geometry())
            overlay.show()
            exclude_from_capture(int(overlay.winId()))
            self._overlays.append(overlay)
        target = next((o for o in self._overlays if o.geometry().contains(QCursor.pos())), self._overlays[0])
        target.activateWindow()
        target.setFocus()

    def sample(self, monitor: MonitorMap, x: int, y: int) -> QImage | None:
        """Pixels ao redor de (x, y), presos às bordas do monitor."""
        bounds = monitor.physical
        left = min(max(bounds.x, x - _RADIUS), bounds.right - (2 * _RADIUS + 1))
        top = min(max(bounds.y, y - _RADIUS), bounds.bottom - (2 * _RADIUS + 1))
        try:
            image = self._capturer.grab(ScreenRect(left, top, 2 * _RADIUS + 1, 2 * _RADIUS + 1))
        except CaptureError:
            return None
        qimage = QImage(image.data, image.shape[1], image.shape[0], image.shape[1] * 3, QImage.Format.Format_RGB888)
        result = qimage.copy()
        result.setText("origin", f"{left},{top}")
        return result

    def pick(self, monitor: MonitorMap, x: int, y: int) -> None:
        if self._done:
            return
        try:
            pixel = self._capturer.grab(ScreenRect(x, y, 1, 1))
        except CaptureError:
            return
        rgb: RGB = (int(pixel[0, 0, 0]), int(pixel[0, 0, 1]), int(pixel[0, 0, 2]))
        self._done = True
        self.picked.emit(rgb, x, y)  # antes de fechar: quem ouve reexibe a janela principal
        self._close()

    def cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self.cancelled.emit()
        self._close()

    def _close(self) -> None:
        for overlay in self._overlays:
            overlay.close()
            overlay.deleteLater()
        self._overlays.clear()
        self._capturer.close()


class _EyedropperOverlay(QWidget):
    def __init__(self, owner: Eyedropper, monitor: MonitorMap) -> None:
        # Dono = diálogo modal ativo (ex.: editor de automação); sem isso o Qt bloqueia o clique.
        super().__init__(
            modal_host(), Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Conta-gotas")
        self.owner = owner
        self.monitor = monitor
        self._cursor: tuple[int, int] | None = None
        self._local: QPointF | None = None
        self._sample: QImage | None = None

    def _to_phys(self, pos: QPointF) -> tuple[int, int]:
        origin = self.geometry().topLeft()
        return self.monitor.to_physical(origin.x() + pos.x(), origin.y() + pos.y())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._local = event.position()
        self._cursor = self._to_phys(self._local)
        self._sample = self.owner.sample(self.monitor, *self._cursor)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.owner.pick(self.monitor, *self._to_phys(event.position()))
        elif event.button() == Qt.MouseButton.RightButton:
            self.owner.cancel()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.owner.cancel()
        else:
            super().keyPressEvent(event)

    def leaveEvent(self, _event: object) -> None:
        self._cursor = None
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        # Alfa mínimo em toda a janela: transparente aos olhos, mas recebe o mouse.
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        self._markers(p)
        self._banner(p)
        if self._cursor is not None and self._local is not None and self._sample is not None:
            self._loupe(p)
        p.end()

    def _loupe(self, p: QPainter) -> None:
        assert self._sample is not None and self._local is not None and self._cursor is not None
        size = (2 * _RADIUS + 1) * _ZOOM
        x = self._local.x() + 24
        y = self._local.y() + 24
        if x + size + 8 > self.width():
            x = self._local.x() - 24 - size
        if y + size + 48 > self.height():
            y = self._local.y() - 24 - size - 40
        box = QRectF(x, y, size, size)

        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)  # pixels nítidos, sem borrão
        p.setPen(QPen(QColor(Color.LINE_STRONG), 1))
        p.setBrush(QColor(Color.SURFACE))
        p.drawRoundedRect(box.adjusted(-6, -6, 6, 46), 12, 12)
        p.drawImage(box, self._sample)

        # O pixel que será escolhido: o do cursor, que pode não ser o central perto das bordas.
        ox, oy = (int(v) for v in self._sample.text("origin").split(","))
        cx, cy = self._cursor[0] - ox, self._cursor[1] - oy
        cell = QRectF(box.x() + cx * _ZOOM, box.y() + cy * _ZOOM, _ZOOM, _ZOOM)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#000000"), 3))
        p.drawRect(cell)
        p.setPen(QPen(QColor("#FFFFFF"), 1.5))
        p.drawRect(cell)

        color = self._sample.pixelColor(cx, cy)
        rgb = (color.red(), color.green(), color.blue())
        swatch = QRectF(box.x(), box.bottom() + 10, 26, 26)
        p.setPen(QPen(QColor(Color.LINE_STRONG), 1))
        p.setBrush(color)
        p.drawRoundedRect(swatch, 5, 5)
        p.setPen(QColor(Color.TEXT))
        p.setFont(display_font(15, QFont.Weight.DemiBold))
        p.drawText(
            QRectF(swatch.right() + 8, swatch.top(), size - 34, 26),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            rgb_to_hex(rgb),
        )
        p.setPen(QColor(Color.TEXT_2))
        p.setFont(display_font(12))
        p.drawText(
            QRectF(swatch.right() + 8, swatch.top(), size - 34, 26),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{self._cursor[0]}, {self._cursor[1]}",
        )

    def _markers(self, p: QPainter) -> None:
        origin = self.geometry().topLeft()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(ui_font(13, QFont.Weight.DemiBold))
        for marker in self.owner.markers:
            if not self.monitor.physical.contains_rect(marker.rect):
                continue
            x, y, w, h = self.monitor.rect_to_logical(marker.rect)
            r = QRectF(x - origin.x(), y - origin.y(), max(w, 1), max(h, 1))
            color = QColor(marker.color)
            if marker.rect.width == 1 and marker.rect.height == 1:
                # Ponto: alvo com cruz, para ver exatamente onde o clique cai.
                c = r.center()
                p.setPen(QPen(color, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, 9, 9)
                p.drawLine(QPointF(c.x() - 14, c.y()), QPointF(c.x() + 14, c.y()))
                p.drawLine(QPointF(c.x(), c.y() - 14), QPointF(c.x(), c.y() + 14))
                anchor = QPointF(c.x() + 14, c.y() - 30)
            else:
                fill = QColor(color)
                fill.setAlpha(30)
                p.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
                p.setBrush(fill)
                p.drawRect(r)
                anchor = QPointF(r.left(), r.top() - 26)
            metrics = p.fontMetrics()
            tag = QRectF(anchor.x(), max(2, anchor.y()), metrics.horizontalAdvance(marker.label) + 14, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(tag, 5, 5)
            p.setPen(QColor(Color.BG))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, marker.label)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _banner(self, p: QPainter) -> None:
        title, hint = self.owner.title, self.owner.hint
        title_font, hint_font = ui_font(15, QFont.Weight.DemiBold), ui_font(14)
        p.setFont(hint_font)
        w = p.fontMetrics().horizontalAdvance(hint) + 40
        box = QRectF((self.width() - w) / 2, 24, w, 62)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(Color.LINE_STRONG), 1))
        p.setBrush(QColor(Color.SURFACE))
        p.drawRoundedRect(box, 12, 12)
        p.setPen(QColor(Color.TEXT))
        p.setFont(title_font)
        p.drawText(box.adjusted(20, 10, -20, -30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        p.setPen(QColor(Color.TEXT_2))
        p.setFont(hint_font)
        p.drawText(box.adjusted(20, 32, -20, -8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, hint)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
