"""Lista de atividades (modelo + delegate), leve o bastante para milhares de linhas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QListView, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from vanthor.core.events import EventKind, EventLevel
from vanthor.ui.theme import icons
from vanthor.ui.theme.fonts import display_font, ui_font
from vanthor.ui.theme.tokens import Color

_KIND_STYLE: dict[EventKind, tuple[str, str]] = {
    EventKind.ENGINE_STARTED: ("play", Color.BRASA),
    EventKind.ENGINE_RESUMED: ("play", Color.BRASA),
    EventKind.ENGINE_PAUSED: ("pause", Color.STEEL),
    EventKind.ENGINE_STOPPED: ("square", Color.STEEL),
    EventKind.EMERGENCY_STOP: ("octagon-x", Color.DANGER),
    EventKind.ERROR: ("circle-alert", Color.DANGER),
    EventKind.DETECTION: ("scan-search", Color.DETECT),
    EventKind.ACTION: ("mouse-pointer-click", Color.BRASA),
    EventKind.SIMULATED_ACTION: ("mouse-pointer-click", Color.SIM),
    EventKind.NOTICE: ("circle-check", Color.DETECT),
}

_ROW_HEIGHT = 40


@dataclass(frozen=True, slots=True)
class ActivityRow:
    timestamp: datetime
    kind: EventKind
    level: EventLevel
    message: str


def format_time(ts: datetime, now: datetime | None = None) -> str:
    local = ts.astimezone()
    today = (now or datetime.now(UTC)).astimezone().date()
    return local.strftime("%H:%M:%S") if local.date() == today else local.strftime("%d/%m %H:%M")


class ActivityModel(QAbstractListModel):
    def __init__(self, max_rows: int = 300) -> None:
        super().__init__()
        self._rows: list[ActivityRow] = []
        self._max = max_rows

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return f"{format_time(row.timestamp)} {row.message}"
        return None

    def replace(self, rows: list[ActivityRow]) -> None:
        self.beginResetModel()
        self._rows = rows[: self._max]
        self.endResetModel()

    def prepend(self, row: ActivityRow) -> None:
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._rows.insert(0, row)
        self.endInsertRows()
        if len(self._rows) > self._max:
            last = len(self._rows) - 1
            self.beginRemoveRows(QModelIndex(), last, last)
            self._rows.pop()
            self.endRemoveRows()


class ActivityDelegate(QStyledItemDelegate):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._time_font = display_font(13)
        self._text_font = ui_font(14)

    def sizeHint(self, _option: QStyleOptionViewItem, _index: QModelIndex | QPersistentModelIndex) -> QSize:
        return QSize(0, _ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        row = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(row, ActivityRow):
            return
        painter.save()
        rect: QRect = option.rect  # type: ignore[attr-defined]
        icon_name, color = _KIND_STYLE.get(row.kind, ("info", Color.TEXT_2))
        y_mid = rect.top() + rect.height() // 2

        painter.setFont(self._time_font)
        painter.setPen(QColor(Color.TEXT_3))
        time_rect = QRect(rect.left() + 4, rect.top(), 76, rect.height())
        painter.drawText(time_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, format_time(row.timestamp))

        pm = icons.pixmap(icon_name, color, 16)
        painter.drawPixmap(rect.left() + 84, y_mid - 8, pm)

        painter.setFont(self._text_font)
        painter.setPen(QColor(Color.DANGER if row.level is not EventLevel.INFO else Color.TEXT))
        text_rect = QRect(rect.left() + 112, rect.top(), rect.width() - 116, rect.height())
        elided = painter.fontMetrics().elidedText(row.message, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
        painter.restore()


class ActivityView(QListView):
    def __init__(self, model: ActivityModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setItemDelegate(ActivityDelegate(self))
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setAccessibleName("Atividade recente")
