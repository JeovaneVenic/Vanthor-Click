"""Automações: criar, editar, duplicar, excluir, ligar/pausar e testar regras."""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vanthor.capture.capturer import CaptureError, RegionCapturer
from vanthor.capture.zones import Zone
from vanthor.core.engine import EngineState
from vanthor.core.events import Event, EventKind
from vanthor.rules.models import Rule, RuleStatus
from vanthor.rules.runtime import RulePhase
from vanthor.ui.context import AppContext
from vanthor.ui.copy import format_count
from vanthor.ui.dialogs.rule_editor import RuleEditorDialog
from vanthor.ui.rule_text import PHASE_LABEL, condition_text, summary, trigger_details
from vanthor.ui.theme import icons
from vanthor.ui.theme.tokens import Color, Size
from vanthor.ui.widgets.toast import Toast
from vanthor.ui.widgets.toggle_switch import ToggleSwitch
from vanthor.vision.color import detect

log = logging.getLogger(__name__)

_PHASE_COLOR: dict[RulePhase, str] = {
    RulePhase.WAITING: Color.TEXT_3,
    RulePhase.CONFIRMING: Color.STEEL,
    RulePhase.PRESENT: Color.DETECT,
    RulePhase.SCHEDULED: Color.BRASA,
    RulePhase.COOLDOWN: Color.STEEL,
    RulePhase.DONE: Color.TEXT_3,
}


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


class _IconButton(QPushButton):
    def __init__(self, icon_name: str, tip: str) -> None:
        super().__init__()
        self.setObjectName("Quiet")
        self.setIcon(icons.icon(icon_name, Color.TEXT_2, 18))
        self.setIconSize(QSize(18, 18))
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(Size.CONTROL_H)


class RuleCard(QFrame):
    def __init__(self, page: RulesPage, rule: Rule, zone: Zone | None) -> None:
        super().__init__()
        self.setObjectName("Panel")
        self.page, self.rule, self.zone = page, rule, zone

        row = QHBoxLayout(self)
        row.setContentsMargins(18, 16, 14, 16)
        row.setSpacing(16)

        self.toggle = ToggleSwitch()
        self.toggle.setChecked(rule.status is RuleStatus.ACTIVE)
        self.toggle.setAccessibleName(f"Ligar {rule.name}")
        self.toggle.setToolTip("Liga ou pausa esta automação")
        self.toggle.toggled.connect(lambda on: page.set_active(self.rule, on))
        row.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(10)
        if zone is not None:
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {zone.color}; border-radius: 5px;")
            head.addWidget(dot)
        name = _label(rule.name)
        name.setStyleSheet("font-size: 16px; font-weight: 600;")
        head.addWidget(name)
        self.chip = _label("", "StatusChip")
        head.addWidget(self.chip)
        head.addStretch(1)
        info.addLayout(head)
        info.addWidget(_label(summary(rule, zone.name if zone else None), "Muted", wrap=True))
        info.addWidget(_label(f"{condition_text(rule.condition)}. {trigger_details(rule.trigger)}.", "Faint", wrap=True))
        self.meta = _label("", "Faint")
        info.addWidget(self.meta)
        row.addLayout(info, 1)

        test = QPushButton("  Testar")
        test.setIcon(icons.icon("scan-search", Color.TEXT, 16))
        test.setCursor(Qt.CursorShape.PointingHandCursor)
        test.setToolTip("Verifica agora se a cor está na zona, sem executar a ação")
        test.clicked.connect(lambda: page.test_rule(self))
        test.setEnabled(zone is not None)
        row.addWidget(test, 0, Qt.AlignmentFlag.AlignTop)
        edit = QPushButton("  Editar")
        edit.setIcon(icons.icon("pencil", Color.TEXT, 16))
        edit.setCursor(Qt.CursorShape.PointingHandCursor)
        edit.clicked.connect(lambda: page.edit_rule(self.rule))
        row.addWidget(edit, 0, Qt.AlignmentFlag.AlignTop)
        duplicate = _IconButton("copy", "Duplicar automação")
        duplicate.clicked.connect(lambda: page.duplicate_rule(self.rule))
        duplicate.setEnabled(zone is not None)
        row.addWidget(duplicate, 0, Qt.AlignmentFlag.AlignTop)
        delete = _IconButton("trash-2", "Excluir automação")
        delete.clicked.connect(lambda: page.delete_rule(self.rule))
        row.addWidget(delete, 0, Qt.AlignmentFlag.AlignTop)
        self._test_text: str | None = None

    def set_chip(self, text: str, color: str) -> None:
        self.chip.setText(text)
        self.chip.setVisible(bool(text))
        self.chip.setStyleSheet(f"color: {color}; border: 1px solid {color};")

    def set_meta(self, text: str) -> None:
        if self._test_text is None:
            self.meta.setText(text)

    def show_test(self, text: str) -> None:
        self._test_text = text
        self.meta.setText(text)
        QTimer.singleShot(5000, self._clear_test)

    def _clear_test(self) -> None:
        self._test_text = None
        self.page.refresh_cards()


class RulesPage(QWidget):
    navigate = Signal(str)

    def __init__(self, ctx: AppContext, toast: Toast, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._toast = toast
        self._cards: dict[int, RuleCard] = {}
        self._pending_delete: set[int] = set()
        self._capturer: RegionCapturer | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(Size.PAGE_MARGIN, 28, Size.PAGE_MARGIN, Size.PAGE_MARGIN)
        root.setSpacing(20)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(_label("Automações", "PageTitle"))
        titles.addWidget(_label("Cada automação observa uma zona e age quando a cor aparece.", "Muted"))
        header.addLayout(titles, 1)
        self._new = QPushButton("  Nova automação")
        self._new.setObjectName("Primary")
        self._new.setIcon(icons.icon("plus", Color.ON_BRASA, 18))
        self._new.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new.clicked.connect(lambda: self.edit_rule(None))
        header.addWidget(self._new, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        host = QWidget()
        self._list = QVBoxLayout(host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(12)
        self._list.addStretch(1)
        self._scroll.setWidget(host)
        root.addWidget(self._scroll, 1)
        self._empty = QFrame()
        self._empty.setObjectName("Panel")
        root.addWidget(self._empty, 1)
        self._build_empty()

        self._live_timer = QTimer(self, interval=300)
        self._live_timer.timeout.connect(lambda: self.refresh_cards(include_counters=False))
        ctx.session.state_changed.connect(lambda _s: self.refresh_cards())
        ctx.session.event_received.connect(self._on_event)

    # -- estados vazios ---------------------------------------------------------------------
    def _build_empty(self) -> None:
        layout = QVBoxLayout(self._empty)
        layout.setContentsMargins(40, 48, 40, 48)
        layout.setSpacing(12)
        layout.addStretch(1)
        icon = QLabel()
        icon.setPixmap(icons.pixmap("zap", Color.TEXT_3, 44, stroke=1.25))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        self._empty_title = _label("", "SectionTitle")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_title)
        self._empty_text = _label("", "Muted", wrap=True)
        self._empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_text.setFixedWidth(480)
        layout.addWidget(self._empty_text, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(8)
        self._empty_button = QPushButton()
        self._empty_button.setObjectName("Primary")
        self._empty_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._empty_button.clicked.connect(self._empty_action)
        layout.addWidget(self._empty_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)

    def _empty_action(self) -> None:
        if not self._zones():
            self.navigate.emit("zones")
        else:
            self.edit_rule(None)

    # -- dados ------------------------------------------------------------------------------
    def _zones(self) -> dict[int, Zone]:
        return {z.id: z for z in self._ctx.zones.list(self._ctx.profiles.active().id)}

    def reload(self) -> None:
        zones = self._zones()
        rules = [r for r in self._ctx.rules.list(self._ctx.profiles.active().id) if r.id not in self._pending_delete]
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        for rule in rules:
            card = RuleCard(self, rule, zones.get(rule.zone_id) if rule.zone_id else None)
            self._list.insertWidget(self._list.count() - 1, card)
            self._cards[rule.id] = card

        has_rules = bool(rules)
        self._scroll.setVisible(has_rules)
        self._empty.setVisible(not has_rules)
        self._new.setEnabled(bool(zones))
        self._new.setToolTip("" if zones else "Marque uma zona antes de criar automações")
        if not zones:
            self._empty_title.setText("Primeiro, marque uma zona")
            self._empty_text.setText("Uma automação observa uma zona da tela. Marque a área que muda de cor e volte aqui.")
            self._empty_button.setText("  Ir para Zonas")
            self._empty_button.setIcon(icons.icon("square-dashed", Color.ON_BRASA, 18))
        else:
            self._empty_title.setText("Nenhuma automação ainda")
            self._empty_text.setText("Diga ao Vanthor qual zona observar, qual cor esperar e o que fazer quando ela aparecer.")
            self._empty_button.setText("  Criar primeira automação")
            self._empty_button.setIcon(icons.icon("plus", Color.ON_BRASA, 18))
        self._empty_text.setMinimumHeight(self._empty_text.heightForWidth(480))
        self.refresh_cards()

    def refresh_cards(self, include_counters: bool = True) -> None:
        """``include_counters=False``: só o status ao vivo (barato, sem consultar o banco)."""
        if not self._cards:
            return
        running = self._ctx.session.state is not EngineState.STOPPED
        live = self._ctx.session.rule_engine.live() if running else {}
        for rule_id, card in self._cards.items():
            rule, zone = card.rule, card.zone
            if include_counters:
                fresh = self._ctx.rules.get(rule_id)
                total = fresh.executions_total if fresh else rule.executions_total
                last = self._ctx.rules.last_fired_at(rule_id)
                when = f", a última às {last.astimezone().strftime('%H:%M:%S')}" if last else ""
                card.set_meta(
                    f"Disparou {format_count(total)} {'vez' if total == 1 else 'vezes'}{when}."
                    if total
                    else "Ainda não disparou."
                )
            if zone is None:
                card.set_chip("Sem zona: edite e escolha uma", Color.DANGER)
            elif rule.status is not RuleStatus.ACTIVE:
                card.set_chip("Pausada", Color.TEXT_3)
            elif not zone.enabled:
                card.set_chip("Zona desligada", Color.DANGER)
            elif running and rule_id in live:
                phase = live[rule_id].phase
                card.set_chip(PHASE_LABEL[phase], _PHASE_COLOR[phase])
            else:
                card.set_chip("", Color.TEXT_3)

    # -- ações --------------------------------------------------------------------------------
    def edit_rule(self, rule: Rule | None) -> None:
        if rule is None and not self._zones():
            self.navigate.emit("zones")
            return
        dialog = RuleEditorDialog(self._ctx, rule, self)
        if dialog.exec() == RuleEditorDialog.DialogCode.Accepted:
            self.reload()
            self._ctx.session.refresh_watch()
            if rule is None and dialog.saved_rule is not None:
                self._toast.show_message(f"{dialog.saved_rule.name} criada", "Ver painel", lambda: self.navigate.emit("dashboard"), lambda: None)

    def set_active(self, rule: Rule, active: bool) -> None:
        status = RuleStatus.ACTIVE if active else RuleStatus.PAUSED
        self._ctx.rules.set_status(rule.id, status)
        card = self._cards.get(rule.id)
        if card is not None:
            card.rule = rule.with_changes(status=status)
        self._ctx.session.refresh_watch()
        self.refresh_cards()

    def duplicate_rule(self, rule: Rule) -> None:
        try:
            copy = self._ctx.rules.duplicate(rule.id)
        except (ValueError, LookupError) as exc:
            log.warning("Não foi possível duplicar: %s", exc)
            return
        self.reload()
        self._toast.show_message(f"{copy.name} criada pausada", "Editar", lambda: self.edit_rule(copy), lambda: None)

    def delete_rule(self, rule: Rule) -> None:
        self._pending_delete.add(rule.id)
        self._ctx.session.reload_rules_excluding(self._pending_delete)
        self.reload()

        def undo() -> None:
            self._pending_delete.discard(rule.id)
            self._ctx.session.reload_rules_excluding(self._pending_delete)
            self.reload()

        def commit() -> None:
            if rule.id in self._pending_delete:
                self._ctx.rules.delete(rule.id)
                self._pending_delete.discard(rule.id)
                self._ctx.session.reload_rules_excluding(self._pending_delete)

        self._toast.show_message(f"{rule.name} excluída", "Desfazer", undo, commit)

    def test_rule(self, card: RuleCard) -> None:
        if card.zone is None:
            return
        if self._capturer is None:
            self._capturer = RegionCapturer()
        try:
            image = self._capturer.grab(card.zone.rect)
        except CaptureError as exc:
            card.show_test(f"Teste: não foi possível ver a zona ({exc}).")
            return
        result = detect(image, card.rule.condition)
        ratio = f"{result.ratio * 100:.1f}".replace(".", ",")
        card.show_test(
            f"Teste agora: cor encontrada em {ratio}% da zona. A automação agiria."
            if result.detected
            else f"Teste agora: cor não encontrada ({ratio}% da zona). A automação continuaria aguardando."
        )

    def _on_event(self, event: Event) -> None:
        if event.kind in (EventKind.ACTION, EventKind.SIMULATED_ACTION) and self.isVisible():
            self.refresh_cards()

    # -- ciclo de vida -----------------------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.reload()
        self._live_timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._live_timer.stop()

    def shutdown(self) -> None:
        self._live_timer.stop()
        if self._capturer is not None:
            self._capturer.close()
            self._capturer = None
