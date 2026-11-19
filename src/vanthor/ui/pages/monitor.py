"""Monitoramento: o que cada automação e cada zona está fazendo agora, e o histórico completo."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vanthor.core.engine import EngineState
from vanthor.core.events import Event, EventKind, EventLevel
from vanthor.rules.models import RuleStatus
from vanthor.ui.context import AppContext
from vanthor.ui.copy import format_count, format_duration
from vanthor.ui.rule_text import PHASE_LABEL
from vanthor.ui.theme.tokens import Color, Size
from vanthor.ui.widgets.activity_list import ActivityModel, ActivityRow, ActivityView
from vanthor.ui.widgets.segmented import Segmented

_FILTERS: dict[str, tuple[set[EventKind] | None, EventLevel | None]] = {
    "all": (None, None),
    "detections": ({EventKind.DETECTION}, None),
    "actions": ({EventKind.ACTION, EventKind.SIMULATED_ACTION}, None),
    "problems": ({EventKind.ERROR, EventKind.EMERGENCY_STOP}, EventLevel.WARNING),
}
_RULE_COLUMNS = ("Automação", "Zona", "Estado", "Cor na zona", "Detecções", "Execuções", "Última detecção")
_ZONE_COLUMNS = ("Zona", "Capturas", "Tempo", "Situação")


def _label(text: str = "", name: str | None = None) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    return label


def _table(columns: tuple[str, ...], stretch: int = 0) -> QTableWidget:
    """``stretch``: a coluna que ocupa a sobra (as outras ficam do tamanho do conteúdo)."""
    table = QTableWidget(0, len(columns))
    table.setObjectName("MonitorTable")
    table.setHorizontalHeaderLabels(list(columns))
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(40)
    table.setShowGrid(False)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setWordWrap(False)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return table


def _cell(table: QTableWidget, row: int, col: int, text: str, color: str | None = None) -> None:
    item = table.item(row, col)
    if item is None:
        item = QTableWidgetItem()
        table.setItem(row, col, item)
    if item.text() != text:
        item.setText(text)
    item.setForeground(QColor(color or Color.TEXT))


def _time(value: datetime | None) -> str:
    return value.astimezone().strftime("%H:%M:%S") if value else "Ainda não"


class MonitorPage(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._rule_rows: list[int] = []
        self._zone_rows: list[int] = []
        self._filter = "all"

        root = QVBoxLayout(self)
        root.setContentsMargins(Size.PAGE_MARGIN, 28, Size.PAGE_MARGIN, Size.PAGE_MARGIN)
        root.setSpacing(18)
        root.addWidget(_label("Monitoramento", "PageTitle"))
        self._summary = _label("", "Muted")
        root.addWidget(self._summary)

        rules_panel = QFrame()
        rules_panel.setObjectName("Panel")
        rp = QVBoxLayout(rules_panel)
        rp.setContentsMargins(18, 14, 18, 10)
        rp.addWidget(_label("Automações agora", "SectionTitle"))
        self._rules = _table(_RULE_COLUMNS)
        rp.addWidget(self._rules)
        root.addWidget(rules_panel, 3)

        bottom = QHBoxLayout()
        bottom.setSpacing(18)
        zones_panel = QFrame()
        zones_panel.setObjectName("Panel")
        zp = QVBoxLayout(zones_panel)
        zp.setContentsMargins(18, 14, 18, 10)
        zp.addWidget(_label("Zonas capturadas", "SectionTitle"))
        self._zones = _table(_ZONE_COLUMNS, stretch=3)
        zp.addWidget(self._zones)
        bottom.addWidget(zones_panel, 4)

        history_panel = QFrame()
        history_panel.setObjectName("Panel")
        hp = QVBoxLayout(history_panel)
        hp.setContentsMargins(18, 14, 12, 10)
        head = QHBoxLayout()
        head.addWidget(_label("Histórico", "SectionTitle"))
        head.addStretch(1)
        self._filter_control = Segmented(
            [("all", "Tudo"), ("detections", "Detecções"), ("actions", "Ações"), ("problems", "Avisos e erros")]
        )
        self._filter_control.set_value("all")
        self._filter_control.changed.connect(self._set_filter)
        head.addWidget(self._filter_control)
        hp.addLayout(head)
        self._history_model = ActivityModel(max_rows=500)
        self._history = ActivityView(self._history_model)
        hp.addWidget(self._history, 1)
        self._history_empty = _label("Nada registrado com este filtro.", "Muted")
        hp.addWidget(self._history_empty)
        bottom.addWidget(history_panel, 5)
        root.addLayout(bottom, 4)

        self._timer = QTimer(self, interval=400)
        self._timer.timeout.connect(self._refresh_live)
        ctx.session.state_changed.connect(lambda _s: self.reload())
        ctx.session.event_received.connect(self._on_event)

    # -- montagem dos dados --------------------------------------------------------------------
    def reload(self) -> None:
        profile_id = self._ctx.profiles.active().id
        zones = {z.id: z for z in self._ctx.zones.list(profile_id)}
        rules = self._ctx.rules.list(profile_id)
        self._zone_names = {zid: z.name for zid, z in zones.items()}
        self._rules_by_id = {r.id: r for r in rules}

        self._rule_rows = [r.id for r in rules]
        self._rules.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            _cell(self._rules, row, 0, rule.name)
            _cell(self._rules, row, 1, self._zone_names.get(rule.zone_id or -1, "Sem zona"))

        plan = self._ctx.session.current_plan()
        self._zone_rows = [z.id for z in plan.zones] if self._ctx.session.state is not EngineState.STOPPED else []
        self._zones.setRowCount(len(self._zone_rows))
        for row, zone_id in enumerate(self._zone_rows):
            _cell(self._zones, row, 0, self._zone_names.get(zone_id, "?"))
        self._load_history()
        self._refresh_live()

    def _refresh_live(self) -> None:
        session = self._ctx.session
        state = session.state
        running = state is not EngineState.STOPPED
        snapshot = session.engine.snapshot()
        rules_count, zones_count = session.watching_counts() if running else (0, 0)
        if running:
            mode = "Modo de teste" if session.dry_run else ("Pausado" if state is EngineState.PAUSED else "Vigiando")
            self._summary.setText(
                f"{mode} há {format_duration(snapshot.active_seconds)}. "
                f"{rules_count} {'automação' if rules_count == 1 else 'automações'} em "
                f"{zones_count} {'zona' if zones_count == 1 else 'zonas'}."
            )
        else:
            self._summary.setText("Parado. Inicie no Painel para acompanhar as automações ao vivo.")

        live = session.rule_engine.live() if running else {}
        for row, rule_id in enumerate(self._rule_rows):
            rule = self._rules_by_id[rule_id]
            info = live.get(rule_id)
            if info is not None:
                phase_color = {
                    "present": Color.DETECT, "scheduled": Color.BRASA, "waiting": Color.TEXT_2,
                }.get(info.phase.value, Color.STEEL)
                _cell(self._rules, row, 2, PHASE_LABEL[info.phase], phase_color)
                ratio = "—" if info.last_ratio is None else f"{info.last_ratio * 100:.1f}%".replace(".", ",")
                _cell(self._rules, row, 3, ratio)
                _cell(self._rules, row, 4, format_count(info.detections))
                _cell(self._rules, row, 5, format_count(info.executions))
                _cell(self._rules, row, 6, _time(info.last_detection))
            else:
                reason = (
                    "Sem zona" if rule.zone_id is None
                    else "Pausada" if rule.status is not RuleStatus.ACTIVE
                    else "Parado" if not running
                    else "Fora da vigia"
                )
                _cell(self._rules, row, 2, reason, Color.DANGER if reason == "Sem zona" else Color.TEXT_3)
                for col in (3, 4, 5, 6):
                    _cell(self._rules, row, col, "—", Color.TEXT_3)

        monitor = session.zone_monitor
        for row, zone_id in enumerate(self._zone_rows):
            status = monitor.status(zone_id)
            _cell(self._zones, row, 1, format_count(status.captures))
            ms = "—" if status.last_capture_ms is None else f"{status.last_capture_ms:.1f} ms".replace(".", ",")
            _cell(self._zones, row, 2, ms)
            if status.error:
                _cell(self._zones, row, 3, status.error, Color.DANGER)
            else:
                _cell(self._zones, row, 3, "Capturando" if status.captures else "Aguardando", Color.DETECT)

    # -- histórico -------------------------------------------------------------------------------
    def _set_filter(self, value: str) -> None:
        self._filter = value
        self._load_history()

    def _load_history(self) -> None:
        kinds, level = _FILTERS[self._filter]
        entries = self._ctx.activity.recent(500, kinds=kinds, min_level=level)
        self._history_model.replace([ActivityRow(e.timestamp, e.kind, e.level, e.message) for e in entries])
        self._history_empty.setVisible(not entries)
        self._history.setVisible(bool(entries))

    def _matches(self, event: Event) -> bool:
        kinds, level = _FILTERS[self._filter]
        if kinds is None and level is None:
            return True
        return bool((kinds and event.kind in kinds) or (level and event.level is not EventLevel.INFO))

    def _on_event(self, event: Event) -> None:
        if self.isVisible() and self._matches(event):
            self._history_model.prepend(ActivityRow(event.timestamp, event.kind, event.level, event.message))
            self._history_empty.hide()
            self._history.show()

    # -- ciclo de vida --------------------------------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.reload()
        self._timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._timer.stop()
