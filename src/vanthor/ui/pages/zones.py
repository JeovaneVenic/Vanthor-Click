"""Zonas: as áreas da tela que o Vanthor observa."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QHideEvent, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from vanthor.actions.models import ClickStep
from vanthor.capture.capturer import CaptureError, Image, RegionCapturer
from vanthor.capture.geometry import MonitorMap, ScreenRect, monitor_for_rect
from vanthor.capture.zones import INTERVAL_CHOICES_MS, Zone, interval_label, next_zone_color
from vanthor.core.engine import EngineState
from vanthor.rules.models import RuleStatus
from vanthor.ui.conceal import conceal_app_windows
from vanthor.ui.context import AppContext
from vanthor.ui.imaging import to_pixmap
from vanthor.ui.copy import format_count
from vanthor.ui.dialogs.color_test import ColorTestDialog
from vanthor.ui.overlays.zone_selector import ZoneSelector
from vanthor.ui.overlays.zones_overlay import ZonesOverlay
from vanthor.ui.screens import monitor_maps
from vanthor.ui.theme import icons
from vanthor.ui.theme.tokens import Color, Size
from vanthor.ui.widgets.toast import Toast
from vanthor.ui.widgets.toggle_switch import ToggleSwitch

log = logging.getLogger(__name__)

_PREVIEW = QSize(176, 99)  # 16:9
_PREVIEW_INTERVAL_MS = 500


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


class ZoneCard(QFrame):
    def __init__(self, page: ZonesPage, zone: Zone) -> None:
        super().__init__()
        self.setObjectName("Panel")
        self.page = page
        self.zone = zone

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 14, 18, 14)
        row.setSpacing(18)

        self.preview = QLabel()
        self.preview.setFixedSize(_PREVIEW + QSize(4, 4))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            f"background: {Color.BG}; border: 2px solid {zone.color}; border-radius: 8px; color: {Color.TEXT_3};"
        )
        self.preview.setAccessibleName(f"Prévia da {zone.name}")
        row.addWidget(self.preview)

        info = QVBoxLayout()
        info.setSpacing(4)
        self.name_edit = QLineEdit(zone.name)
        self.name_edit.setObjectName("InlineName")
        self.name_edit.setMaxLength(40)
        self.name_edit.setToolTip("Clique para renomear")
        self.name_edit.setAccessibleName("Nome da zona")
        self.name_edit.editingFinished.connect(self._rename)
        info.addWidget(self.name_edit)
        self.coords = _label("", "Faint")
        info.addWidget(self.coords)
        self.status = _label("", "Faint", wrap=True)
        info.addWidget(self.status)
        info.addStretch(1)
        row.addLayout(info, 1)

        controls = QVBoxLayout()
        controls.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.interval = QComboBox()
        self.interval.setAccessibleName("Frequência de análise")
        self.interval.setToolTip("Com que frequência a zona é analisada. Mais rápido usa mais processador.")
        choices = sorted({*INTERVAL_CHOICES_MS, zone.interval_ms})
        for ms in choices:
            self.interval.addItem(interval_label(ms), ms)
        self.interval.setCurrentIndex(choices.index(zone.interval_ms))
        self.interval.currentIndexChanged.connect(self._change_interval)
        top.addWidget(self.interval)

        self.enabled = ToggleSwitch()
        self.enabled.setChecked(zone.enabled)
        self.enabled.setAccessibleName("Zona ligada")
        self.enabled.setToolTip("Liga ou desliga a observação desta zona")
        self.enabled.toggled.connect(self._toggle)
        top.addWidget(self.enabled)
        controls.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addStretch(1)
        test = QPushButton("  Testar cor")
        test.setIcon(icons.icon("scan-search", Color.TEXT, 16))
        test.setCursor(Qt.CursorShape.PointingHandCursor)
        test.setToolTip("Escolha uma cor e veja ao vivo se ela aparece nesta zona")
        test.clicked.connect(lambda: page.test_color(self.zone))
        bottom.addWidget(test)
        adjust = QPushButton("  Ajustar na tela")
        adjust.setIcon(icons.icon("move", Color.TEXT, 16))
        adjust.setCursor(Qt.CursorShape.PointingHandCursor)
        adjust.clicked.connect(lambda: page.select_on_screen(self.zone))
        bottom.addWidget(adjust)
        delete = QPushButton()
        delete.setObjectName("Quiet")
        delete.setIcon(icons.icon("trash-2", Color.TEXT_2, 18))
        delete.setToolTip("Excluir zona")
        delete.setAccessibleName(f"Excluir {zone.name}")
        delete.setCursor(Qt.CursorShape.PointingHandCursor)
        delete.setFixedWidth(Size.CONTROL_H)
        delete.clicked.connect(lambda: page.delete_zone(self.zone))
        bottom.addWidget(delete)
        controls.addLayout(bottom)
        row.addLayout(controls)

    def refresh_text(self, monitors: list[MonitorMap]) -> None:
        r = self.zone.rect
        monitor = monitor_for_rect(r, monitors)
        where = monitor.label if monitor else "Fora da tela"
        self.coords.setText(f"{where}     X {r.x}   Y {r.y}     {r.width} × {r.height} px")
        self.preview.setStyleSheet(
            f"background: {Color.BG}; border: 2px solid {self.zone.color}; border-radius: 8px;"
            f" color: {Color.TEXT_3};"
        )

    def show_status(self, text: str, *, error: bool) -> None:
        self.status.setObjectName("FieldError" if error else "Faint")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)

    def _rename(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.name_edit.setText(self.zone.name)
            return
        if name != self.zone.name:
            self.page.save_zone(self.zone.with_changes(name=name))

    def _change_interval(self) -> None:
        self.page.save_zone(self.zone.with_changes(interval_ms=int(self.interval.currentData())))

    def _toggle(self, enabled: bool) -> None:
        self.page.save_zone(self.zone.with_changes(enabled=enabled))


class ZonesPage(QWidget):
    def __init__(self, ctx: AppContext, toast: Toast, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._toast = toast
        self._cards: dict[int, ZoneCard] = {}
        self._pending_delete: set[int] = set()
        self._selector: ZoneSelector | None = None
        self._restore_windows: Callable[[], None] | None = None
        self._overlay = ZonesOverlay()
        self._overlay_wanted = False
        self._capturer: RegionCapturer | None = None
        self._monitors: list[MonitorMap] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Size.PAGE_MARGIN, 28, Size.PAGE_MARGIN, Size.PAGE_MARGIN)
        root.setSpacing(20)

        header = QHBoxLayout()
        header.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(_label("Zonas", "PageTitle"))
        titles.addWidget(_label("Áreas da tela que o Vanthor observa. Cada zona fica dentro de um monitor.", "Muted"))
        header.addLayout(titles, 1)
        self._show_button = QPushButton()
        self._show_button.setCheckable(True)
        self._show_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._show_button.setToolTip("Desenha as zonas por cima da tela. Não interfere em cliques nem na captura.")
        self._show_button.toggled.connect(self._toggle_overlay)
        header.addWidget(self._show_button, 0, Qt.AlignmentFlag.AlignBottom)
        self._new_button = QPushButton("  Nova zona")
        self._new_button.setObjectName("Primary")
        self._new_button.setIcon(icons.icon("plus", Color.ON_BRASA, 18))
        self._new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_button.clicked.connect(lambda: self.select_on_screen(None))
        header.addWidget(self._new_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._list_host = QWidget()
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(12)
        self._list.addStretch(1)
        self._scroll.setWidget(self._list_host)
        root.addWidget(self._scroll, 1)

        self._empty = self._build_empty()
        root.addWidget(self._empty, 1)

        self._preview_timer = QTimer(self, interval=_PREVIEW_INTERVAL_MS)
        self._preview_timer.timeout.connect(self._refresh_previews)
        ctx.session.state_changed.connect(lambda _s: self._refresh_previews())
        self._sync_show_button()
        self.reload()

    # -- montagem ---------------------------------------------------------------------
    def _build_empty(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(40, 48, 40, 48)
        layout.setSpacing(12)
        layout.addStretch(1)
        icon = QLabel()
        icon.setPixmap(icons.pixmap("square-dashed", Color.TEXT_3, 44, stroke=1.25))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        title = _label("Nenhuma zona ainda", "SectionTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        text = _label(
            "Uma zona é a área da tela que o Vanthor observa para decidir quando agir. "
            "Marque o botão, a luz ou o campo que muda de cor.",
            "Muted",
            wrap=True,
        )
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Largura FIXA: com só largura máxima, o Qt calcula a altura do texto quebrado errado e corta.
        text.setFixedWidth(460)
        text.setMinimumHeight(text.heightForWidth(460))
        layout.addWidget(text, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(8)
        button = QPushButton("  Marcar primeira zona")
        button.setObjectName("Primary")
        button.setIcon(icons.icon("square-dashed", Color.ON_BRASA, 18))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: self.select_on_screen(None))
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)
        return panel

    # -- dados ------------------------------------------------------------------------
    def _zones(self) -> list[Zone]:
        zones = self._ctx.zones.list(self._ctx.profiles.active().id)
        return [z for z in zones if z.id not in self._pending_delete]

    def reload(self) -> None:
        zones = self._zones()
        self._monitors = monitor_maps()
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        for zone in zones:
            card = ZoneCard(self, zone)
            card.refresh_text(self._monitors)
            self._list.insertWidget(self._list.count() - 1, card)
            self._cards[zone.id] = card
        has_zones = bool(zones)
        self._scroll.setVisible(has_zones)
        self._empty.setVisible(not has_zones)
        self._show_button.setEnabled(has_zones)
        if self._overlay.visible:
            self._overlay.update_zones(zones)
        self._refresh_previews()

    def save_zone(self, zone: Zone) -> None:
        try:
            self._ctx.zones.update(zone)
        except ValueError as exc:
            log.warning("Zona inválida: %s", exc)
            self.reload()
            return
        card = self._cards.get(zone.id)
        if card is not None:
            card.zone = zone
            card.refresh_text(self._monitors)
        self._after_change()

    def delete_zone(self, zone: Zone) -> None:
        self._pending_delete.add(zone.id)
        card = self._cards.pop(zone.id, None)
        if card is not None:
            card.hide()
            card.deleteLater()
        self._after_change(rebuild=not self._cards)

        def undo() -> None:
            self._pending_delete.discard(zone.id)
            self.reload()
            self._after_change()

        def commit() -> None:
            if zone.id in self._pending_delete:
                self._ctx.zones.delete(zone.id)
                self._pending_delete.discard(zone.id)

        self._toast.show_message(f"{zone.name} excluída", "Desfazer", undo, commit)

    def _after_change(self, rebuild: bool = False) -> None:
        if rebuild:
            self.reload()
        self._ctx.session.reload_zones_excluding(self._pending_delete)
        if self._overlay.visible:
            self._overlay.update_zones(self._zones())

    # -- testar cor -----------------------------------------------------------------------
    def test_color(self, zone: Zone) -> None:
        self._preview_timer.stop()  # o diálogo captura a zona por conta própria
        dialog = ColorTestDialog(self._ctx, zone, self)
        dialog.finished.connect(lambda _r: (self._preview_timer.start(), dialog.deleteLater()))
        dialog.open()

    # -- seleção na tela ----------------------------------------------------------------
    def select_on_screen(self, zone: Zone | None) -> None:
        if self._selector is not None:
            return
        existing = self._zones()
        color = zone.color if zone else next_zone_color([z.color for z in existing])
        title = f"Ajustar {zone.name}" if zone else "Nova zona"
        selector = ZoneSelector(color, title, zone.rect if zone else None)
        selector.selected.connect(lambda rect, monitor: self._on_selected(zone, rect, monitor))
        selector.cancelled.connect(self._restore_window)
        self._selector = selector
        self._overlay.hide()
        self._restore_windows = conceal_app_windows()
        # Um instante para o Windows redesenhar sem as nossas janelas antes de cobrir a tela.
        QTimer.singleShot(120, selector.start)

    def _on_selected(self, zone: Zone | None, rect: ScreenRect, monitor: str) -> None:
        if zone is None:
            self._ctx.zones.create(self._ctx.profiles.active().id, rect, monitor)
        else:
            self._ctx.zones.update(zone.with_changes(rect=rect, monitor=monitor))
        self._restore_window()
        self.reload()
        self._after_change()

    def _restore_window(self) -> None:
        self._selector = None
        if self._restore_windows is not None:
            self._restore_windows()
            self._restore_windows = None
        if self._overlay_wanted:
            self._overlay.show(self._zones(), self._click_points())

    # -- visualização na tela ------------------------------------------------------------
    def _click_points(self) -> list[tuple[int, int, str]]:
        """Pontos de clique das automações ativas, para desenhar junto com as zonas."""
        points: list[tuple[int, int, str]] = []
        for rule in self._ctx.rules.list(self._ctx.profiles.active().id):
            if rule.status is not RuleStatus.ACTIVE:
                continue
            clicks = [s for s in rule.action.steps if isinstance(s, ClickStep)]
            for index, step in enumerate(clicks, start=1):
                suffix = f" ({index})" if len(clicks) > 1 else ""
                points.append((step.x, step.y, f"Clique: {rule.name}{suffix}"))
        return points

    def _toggle_overlay(self, on: bool) -> None:
        self._overlay_wanted = on
        if on:
            self._overlay.show(self._zones(), self._click_points())
        else:
            self._overlay.hide()
        self._sync_show_button()

    def _sync_show_button(self) -> None:
        on = self._show_button.isChecked()
        self._show_button.setText("  Ocultar da tela" if on else "  Mostrar na tela")
        self._show_button.setIcon(icons.icon("eye-off" if on else "eye", Color.TEXT, 16))

    def hide_overlay(self) -> None:
        self._show_button.setChecked(False)
        self._overlay.hide()

    # -- prévias ---------------------------------------------------------------------------
    def _refresh_previews(self) -> None:
        if not self.isVisible() or not self._cards:
            return
        running = self._ctx.session.state is not EngineState.STOPPED
        monitor = self._ctx.session.zone_monitor
        screens = [m.physical for m in self._monitors]
        ratio = self.devicePixelRatioF()
        for zone_id, card in self._cards.items():
            zone = card.zone
            if not any(s.contains_rect(zone.rect) for s in screens):
                card.preview.setPixmap(QPixmap())
                card.preview.setText("Fora da tela")
                card.show_status("O monitor desta zona não está conectado. Ajuste a zona na tela.", error=True)
                continue
            image: Image | None = None
            if running and zone.enabled:
                # Durante a automação, reaproveita o quadro do motor: nada de capturar duas vezes.
                frame = monitor.latest_frame(zone_id)
                image = frame.image if frame else None
                status = monitor.status(zone_id)
                if status.error:
                    card.show_status(status.error, error=True)
                elif status.last_capture_ms is not None:
                    ms = f"{status.last_capture_ms:.1f}".replace(".", ",")
                    card.show_status(
                        f"Observando: {format_count(status.captures)} capturas, {ms} ms cada", error=False
                    )
            else:
                card.show_status("" if zone.enabled else "Desligada: não é observada durante a automação.", error=False)
            if image is None:
                image = self._grab(zone.rect)
            if image is not None:
                card.preview.setText("")
                card.preview.setPixmap(to_pixmap(image, _PREVIEW, ratio))

    def _grab(self, rect: ScreenRect) -> Image | None:
        if self._capturer is None:
            self._capturer = RegionCapturer()
        try:
            return self._capturer.grab(rect)
        except CaptureError:
            log.warning("Prévia indisponível para %s", rect, exc_info=True)
            return None

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.reload()
        self._preview_timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._preview_timer.stop()

    def shutdown(self) -> None:
        self._toast.flush()
        self._overlay.hide()
        self._preview_timer.stop()
        if self._capturer is not None:
            self._capturer.close()
            self._capturer = None
