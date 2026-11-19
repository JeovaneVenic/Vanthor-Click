"""Seleção de zona direto na tela: arrastar para marcar, alças para ajustar, arrastar para mover.

Uma janela transparente por monitor (cada monitor pode ter uma escala diferente). Toda a lógica
de retângulo é em pixels físicos; a tela só converte para desenhar.
"""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QObject
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from vanthor.capture.geometry import MonitorMap, ScreenRect
from vanthor.capture.zones import MIN_ZONE_SIDE
from vanthor.ui.overlays.host import modal_host
from vanthor.ui.screens import monitor_maps, screen_for_map
from vanthor.ui.theme.fonts import display_font, ui_font
from vanthor.ui.theme.tokens import Color
from vanthor.winsys.desktop import exclude_from_capture

_HANDLE = 9  # px lógicos
_DIM = QColor(8, 9, 10, 160)  # escuro o bastante para a zona "acender" por contraste


class _Drag(Enum):
    NONE = auto()
    DRAW = auto()
    MOVE = auto()
    RESIZE = auto()


class ZoneSelector(QObject):
    """Coordena as janelas de seleção. Emite ``selected`` ou ``cancelled`` uma única vez."""

    selected = Signal(object, str)  # ScreenRect, nome do monitor
    cancelled = Signal()

    def __init__(self, color: str, title: str, initial: ScreenRect | None = None) -> None:
        super().__init__()
        self.color = color
        self.title = title
        self.rect: ScreenRect | None = initial
        self.monitors = monitor_maps()
        self.monitor: MonitorMap | None = None
        if initial is not None:
            self.monitor = next((m for m in self.monitors if m.physical.contains_rect(initial)), None)
            if self.monitor is None:  # zona antiga fora das telas atuais: começa do zero
                self.rect = None
        self._overlays: list[_SelectionOverlay] = []
        self._done = False

    def start(self) -> None:
        for monitor in self.monitors:
            screen = screen_for_map(monitor)
            if screen is None:
                continue
            overlay = _SelectionOverlay(self, monitor)
            overlay.setGeometry(screen.geometry())
            overlay.show()
            exclude_from_capture(int(overlay.winId()))
            self._overlays.append(overlay)
        target = next((o for o in self._overlays if o.monitor is self.monitor), None) or next(
            (o for o in self._overlays if o.geometry().contains(QCursor.pos())), self._overlays[0]
        )
        target.activateWindow()
        target.raise_()
        target.setFocus()

    # -- chamado pelas janelas -----------------------------------------------------------
    def set_rect(self, rect: ScreenRect | None, monitor: MonitorMap) -> None:
        self.rect, self.monitor = rect, monitor
        for overlay in self._overlays:
            overlay.sync()

    def nudge(self, dx: int, dy: int, resize: bool) -> None:
        if self.rect is None or self.monitor is None:
            return
        r, bounds = self.rect, self.monitor.physical
        if resize:
            width = min(max(MIN_ZONE_SIDE, r.width + dx), bounds.right - r.x)
            height = min(max(MIN_ZONE_SIDE, r.height + dy), bounds.bottom - r.y)
            new = ScreenRect(r.x, r.y, width, height)
        else:
            x = min(max(bounds.x, r.x + dx), bounds.right - r.width)
            y = min(max(bounds.y, r.y + dy), bounds.bottom - r.height)
            new = ScreenRect(x, y, r.width, r.height)
        self.set_rect(new, self.monitor)

    def confirm(self) -> None:
        if self._done or self.rect is None or self.monitor is None:
            return
        self._done = True
        # Sinal ANTES de fechar: quem ouve reexibe a janela principal. Se as sobreposições
        # fechassem primeiro, o Qt veria "nenhuma janela aberta" e encerraria o aplicativo.
        self.selected.emit(self.rect, self.monitor.name)
        self._close_overlays()

    def cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self.cancelled.emit()
        self._close_overlays()

    def _close_overlays(self) -> None:
        for overlay in self._overlays:
            overlay.close()
            overlay.deleteLater()
        self._overlays.clear()


class _SelectionOverlay(QWidget):
    def __init__(self, selector: ZoneSelector, monitor: MonitorMap) -> None:
        # Dono = diálogo modal ativo, se houver; sem isso o Qt bloqueia mouse e teclado.
        super().__init__(
            modal_host(),
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Seleção de zona")
        self.selector = selector
        self.monitor = monitor
        self._drag = _Drag.NONE
        self._anchor: tuple[int, int] = (0, 0)
        self._start_rect: ScreenRect | None = None
        self._edges = ""  # combinação de "l", "r", "t", "b" da alça em uso
        self._cursor_phys: tuple[int, int] | None = None

        self._toolbar = QWidget(self)
        self._toolbar.setObjectName("SelectorToolbar")
        self._toolbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._toolbar.setStyleSheet(
            f"#SelectorToolbar {{ background: {Color.SURFACE}; border: 1px solid {Color.LINE_STRONG};"
            " border-radius: 12px; }"
        )
        bar = QHBoxLayout(self._toolbar)
        bar.setContentsMargins(6, 6, 6, 6)
        bar.setSpacing(6)
        cancel = QPushButton("Cancelar")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(selector.cancel)
        confirm = QPushButton("Confirmar zona")
        confirm.setObjectName("Primary")
        confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm.setMinimumHeight(40)
        confirm.clicked.connect(selector.confirm)
        bar.addWidget(cancel)
        bar.addWidget(confirm)
        self._toolbar.adjustSize()
        self._toolbar.hide()

    # -- conversões ---------------------------------------------------------------------
    def _to_phys(self, pos: QPointF) -> tuple[int, int]:
        origin = self.geometry().topLeft()
        return self.monitor.to_physical(origin.x() + pos.x(), origin.y() + pos.y())

    def _local_rect(self, rect: ScreenRect) -> QRectF:
        x, y, w, h = self.monitor.rect_to_logical(rect)
        origin = self.geometry().topLeft()
        return QRectF(x - origin.x(), y - origin.y(), w, h)

    def _my_rect(self) -> ScreenRect | None:
        s = self.selector
        return s.rect if s.rect is not None and s.monitor is self.monitor else None

    def sync(self) -> None:
        rect = self._my_rect()
        if rect is None:
            self._toolbar.hide()
        else:
            local = self._local_rect(rect)
            tw, th = self._toolbar.width(), self._toolbar.height()
            x = min(max(8, local.right() - tw), self.width() - tw - 8)
            y = local.bottom() + 12
            if y + th > self.height() - 8:
                y = max(8, local.top() - th - 12)
            self._toolbar.move(int(x), int(y))
            self._toolbar.show()
        self.update()

    def showEvent(self, event: object) -> None:  # noqa: D401
        super().showEvent(event)  # type: ignore[arg-type]
        self.sync()

    # -- mouse --------------------------------------------------------------------------
    def _hit_edges(self, pos: QPointF) -> str:
        rect = self._my_rect()
        if rect is None:
            return ""
        r = self._local_rect(rect)
        near = _HANDLE
        edges = ""
        if abs(pos.x() - r.left()) <= near and r.top() - near <= pos.y() <= r.bottom() + near:
            edges += "l"
        if abs(pos.x() - r.right()) <= near and r.top() - near <= pos.y() <= r.bottom() + near:
            edges += "r"
        if abs(pos.y() - r.top()) <= near and r.left() - near <= pos.x() <= r.right() + near:
            edges += "t"
        if abs(pos.y() - r.bottom()) <= near and r.left() - near <= pos.x() <= r.right() + near:
            edges += "b"
        return edges

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        phys = self._to_phys(pos)
        edges = self._hit_edges(pos)
        rect = self._my_rect()
        if edges:
            self._drag, self._edges = _Drag.RESIZE, edges
        elif rect is not None and self._local_rect(rect).contains(pos):
            self._drag = _Drag.MOVE
        else:
            self._drag = _Drag.DRAW
            self.selector.set_rect(None, self.monitor)
        self._anchor = phys
        self._start_rect = self._my_rect()
        self.activateWindow()
        self.setFocus()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        phys = self._to_phys(pos)
        self._cursor_phys = phys
        if self._drag is _Drag.DRAW:
            self.selector.set_rect(ScreenRect.from_points(*self._anchor, *phys), self.monitor)
        elif self._drag is _Drag.MOVE and self._start_rect is not None:
            r, bounds = self._start_rect, self.monitor.physical
            x = min(max(bounds.x, r.x + phys[0] - self._anchor[0]), bounds.right - r.width)
            y = min(max(bounds.y, r.y + phys[1] - self._anchor[1]), bounds.bottom - r.height)
            self.selector.set_rect(ScreenRect(x, y, r.width, r.height), self.monitor)
        elif self._drag is _Drag.RESIZE and self._start_rect is not None:
            r = self._start_rect
            left, top, right, bottom = r.x, r.y, r.right - 1, r.bottom - 1
            if "l" in self._edges:
                left = phys[0]
            if "r" in self._edges:
                right = phys[0]
            if "t" in self._edges:
                top = phys[1]
            if "b" in self._edges:
                bottom = phys[1]
            self.selector.set_rect(ScreenRect.from_points(left, top, right, bottom), self.monitor)
        else:
            self._update_cursor(pos)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self._my_rect()
        if self._drag is _Drag.DRAW and rect is not None and (
            rect.width < MIN_ZONE_SIDE or rect.height < MIN_ZONE_SIDE
        ):
            # Um clique sem arrastar não vira zona de 1 pixel por acidente.
            self.selector.set_rect(None, self.monitor)
        self._drag = _Drag.NONE
        self._update_cursor(event.position())

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        rect = self._my_rect()
        if rect is not None and self._local_rect(rect).contains(event.position()):
            self.selector.confirm()

    def _update_cursor(self, pos: QPointF) -> None:
        edges = self._hit_edges(pos)
        rect = self._my_rect()
        shapes = {
            "lt": Qt.CursorShape.SizeFDiagCursor, "rb": Qt.CursorShape.SizeFDiagCursor,
            "rt": Qt.CursorShape.SizeBDiagCursor, "lb": Qt.CursorShape.SizeBDiagCursor,
            "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
            "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
        }
        key = "".join(sorted(edges, key="lrtb".index)) if edges else ""
        key = {"lt": "lt", "rb": "rb", "rt": "rt", "lb": "lb"}.get(key, key[:1])
        if key in shapes:
            self.setCursor(shapes[key])
        elif rect is not None and self._local_rect(rect).contains(pos):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    # -- teclado ------------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.selector.cancel()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.selector.confirm()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(Qt.Key(key), 0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(Qt.Key(key), 0)
            resize = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self.selector.nudge(dx, dy, resize)
        else:
            super().keyPressEvent(event)

    # -- desenho ------------------------------------------------------------------------
    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = QRectF(self.rect())
        rect = self._my_rect()
        color = QColor(self.selector.color)

        p.fillRect(full, _DIM)
        if rect is not None:
            local = self._local_rect(rect)
            # "Fura" o escurecido para mostrar a tela real; alfa 1 mantém os cliques nesta janela.
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(local, QColor(0, 0, 0, 1))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(color, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(local.adjusted(-1, -1, 1, 1))
            self._draw_handles(p, local, color)
            self._pill(
                p,
                f"{rect.width} × {rect.height} px",
                QPointF(local.left(), local.top() - 10),
                anchor_bottom=True,
            )
        elif self._cursor_phys is not None and self._drag is _Drag.NONE:
            self._draw_crosshair(p, color)

        hint = (
            "Arraste para marcar a área que o Vanthor vai observar."
            if rect is None
            else "Arraste para mover, use as alças para ajustar. Enter confirma, Esc cancela."
        )
        title = f"{self.selector.title}   {self.monitor.label}"
        self._banner(p, title, hint)
        p.end()

    def _draw_handles(self, p: QPainter, r: QRectF, color: QColor) -> None:
        p.setPen(QPen(color, 1.5))
        p.setBrush(QColor(Color.TEXT))
        half = _HANDLE / 2
        xs = (r.left(), r.center().x(), r.right())
        ys = (r.top(), r.center().y(), r.bottom())
        for x in xs:
            for y in ys:
                if x == r.center().x() and y == r.center().y():
                    continue
                p.drawRoundedRect(QRectF(x - half, y - half, _HANDLE, _HANDLE), 2, 2)

    def _draw_crosshair(self, p: QPainter, color: QColor) -> None:
        if self._cursor_phys is None:
            return
        lx, ly = self.monitor.to_logical(*self._cursor_phys)
        origin = self.geometry().topLeft()
        x, y = lx - origin.x(), ly - origin.y()
        line = QColor(color)
        line.setAlpha(140)
        p.setPen(QPen(line, 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        self._pill(p, f"X {self._cursor_phys[0]}   Y {self._cursor_phys[1]}", QPointF(x + 14, y + 14))

    def _pill(self, p: QPainter, text: str, at: QPointF, anchor_bottom: bool = False) -> None:
        font = display_font(14, QFont.Weight.DemiBold)
        p.setFont(font)
        metrics = p.fontMetrics()
        w, h = metrics.horizontalAdvance(text) + 20, metrics.height() + 10
        x = min(max(4, at.x()), self.width() - w - 4)
        y = at.y() - h if anchor_bottom else at.y()
        y = min(max(4, y), self.height() - h - 4)
        box = QRectF(x, y, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Color.SURFACE))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor(Color.TEXT))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _banner(self, p: QPainter, title: str, hint: str) -> None:
        title_font = ui_font(15, QFont.Weight.DemiBold)
        hint_font = ui_font(14)
        p.setFont(title_font)
        tw = p.fontMetrics().horizontalAdvance(title)
        p.setFont(hint_font)
        hw = p.fontMetrics().horizontalAdvance(hint)
        w = max(tw, hw) + 40
        box = QRectF((self.width() - w) / 2, 24, w, 62)
        p.setPen(QPen(QColor(Color.LINE_STRONG), 1))
        p.setBrush(QColor(Color.SURFACE))
        p.drawRoundedRect(box, 12, 12)
        p.setPen(QColor(Color.TEXT))
        p.setFont(title_font)
        p.drawText(box.adjusted(20, 10, -20, -30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        p.setPen(QColor(Color.TEXT_2))
        p.setFont(hint_font)
        p.drawText(box.adjusted(20, 32, -20, -8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, hint)
