"""Editor de automação em três etapas: O que observar? Quando executar? O que fazer?"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vanthor.capture.capturer import CaptureError, Image, RegionCapturer
from vanthor.capture.zones import Zone
from vanthor.rules.models import (
    MIN_CONTINUOUS_COOLDOWN_MS,
    FireMode,
    Rule,
    RuleStatus,
    TriggerSettings,
)
from vanthor.ui.context import AppContext
from vanthor.ui.dialogs.color_test import ColorTestDialog
from vanthor.ui.imaging import to_pixmap
from vanthor.ui.rule_text import MODE_HELP, MODE_LABEL, condition_text, seconds
from vanthor.ui.theme import icons
from vanthor.ui.theme.fonts import display_font
from vanthor.ui.theme.tokens import Color, Size
from vanthor.ui.widgets.action_editor import ActionEditor
from vanthor.ui.widgets.segmented import Segmented
from vanthor.ui.widgets.wrap_label import WrapLabel
from vanthor.vision.color import ColorCondition, detect, suggest_color

log = logging.getLogger(__name__)

_THUMB = QSize(150, 84)
_STEPS = ("O que observar?", "Quando executar?", "O que fazer?")


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


class RuleEditorDialog(QDialog):
    def __init__(self, ctx: AppContext, rule: Rule | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._rule = rule
        self._profile_id = ctx.profiles.active().id
        self._zones = [z for z in ctx.zones.list(self._profile_id)]
        self._capturer = RegionCapturer()
        self._conditions: dict[int, ColorCondition] = {}  # condição escolhida por zona nesta edição
        self._step = 0
        self.saved_rule: Rule | None = None

        self.setWindowTitle("Editar automação" if rule else "Nova automação")
        self.setMinimumSize(900, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(Size.PAGE_MARGIN, 26, Size.PAGE_MARGIN, 24)
        root.setSpacing(18)
        root.addWidget(_label(self.windowTitle(), "PageTitle"))
        root.addLayout(self._build_stepper())

        self._pages = QStackedWidget()
        self._pages.addWidget(self._scroll(self._build_observe()))
        self._pages.addWidget(self._scroll(self._build_when()))
        self._pages.addWidget(self._scroll(self._build_do()))
        root.addWidget(self._pages, 1)

        self._error = _label("", "FieldError", wrap=True)
        self._error.hide()
        root.addWidget(self._error)
        root.addLayout(self._build_footer())

        self._live = QTimer(self, interval=500)
        self._live.timeout.connect(self._refresh_live)

        self._load()
        self._step = 0
        self._go(0)
        self._live.start()

    # -- estrutura -------------------------------------------------------------------------
    @staticmethod
    def _scroll(content: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(content)
        return area

    def _build_stepper(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._step_buttons: list[QToolButton] = []
        for index, title in enumerate(_STEPS):
            button = QToolButton()
            button.setObjectName("StepButton")
            button.setText(f"{index + 1}   {title}")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self._go(i))
            row.addWidget(button)
            self._step_buttons.append(button)
            if index < len(_STEPS) - 1:
                line = QFrame()
                line.setObjectName("Divider")
                line.setFixedHeight(1)
                row.addWidget(line, 1)
        return row

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        cancel = QPushButton("Cancelar")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addStretch(1)
        self._back = QPushButton("  Voltar")
        self._back.setIcon(icons.icon("arrow-left", Color.TEXT, 16))
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.clicked.connect(lambda: self._go(self._step - 1))
        row.addWidget(self._back)
        self._next = QPushButton()
        self._next.setObjectName("Primary")
        self._next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next.setMinimumWidth(190)
        self._next.clicked.connect(self._advance)
        row.addWidget(self._next)
        return row

    # -- etapa 1: o que observar -------------------------------------------------------------
    def _build_observe(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(10)

        col.addWidget(_label("Nome", "SectionTitle"))
        self._name = QLineEdit()
        self._name.setMaxLength(60)
        self._name.setPlaceholderText("Ex.: Botão comprar ficou verde")
        self._name.setAccessibleName("Nome da automação")
        col.addWidget(self._name)
        col.addSpacing(10)

        col.addWidget(_label("Zona observada", "SectionTitle"))
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self._zone_group = QButtonGroup(self)
        self._zone_group.setExclusive(True)
        self._zone_buttons: dict[int, QToolButton] = {}
        ratio = self.devicePixelRatioF()
        for index, zone in enumerate(self._zones):
            button = QToolButton()
            button.setObjectName("ZoneChoice")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setText(zone.name if zone.enabled else f"{zone.name} (desligada)")
            button.setIconSize(_THUMB)
            image = self._grab(zone)
            if image is not None:
                button.setIcon(to_pixmap(image, _THUMB, ratio))
            button.setStyleSheet(f"QToolButton#ZoneChoice:checked {{ border: 2px solid {zone.color}; }}")
            button.clicked.connect(lambda _=False, z=zone: self._zone_selected(z))
            self._zone_group.addButton(button)
            self._zone_buttons[zone.id] = button
            grid.addWidget(button, index // 4, index % 4)
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        col.addWidget(grid_host)
        col.addSpacing(10)

        col.addWidget(_label("Cor procurada", "SectionTitle"))
        color_box = QFrame()
        color_box.setObjectName("Panel")
        row = QHBoxLayout(color_box)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(14)
        self._swatch = QLabel()
        self._swatch.setFixedSize(40, 40)
        row.addWidget(self._swatch)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self._condition_label = _label()
        self._condition_label.setFont(display_font(16, QFont.Weight.DemiBold))
        self._live_label = _label("", "Muted", wrap=True)
        texts.addWidget(self._condition_label)
        texts.addWidget(self._live_label)
        row.addLayout(texts, 1)
        adjust = QPushButton("  Ajustar cor")
        adjust.setIcon(icons.icon("crosshair", Color.TEXT, 16))
        adjust.setCursor(Qt.CursorShape.PointingHandCursor)
        adjust.clicked.connect(self._adjust_color)
        row.addWidget(adjust)
        col.addWidget(color_box)
        col.addStretch(1)
        return page

    # -- etapa 2: quando executar ------------------------------------------------------------
    def _build_when(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(10)
        col.addWidget(_label("Quando agir", "SectionTitle"))
        self._mode = Segmented([(m.value, MODE_LABEL[m]) for m in FireMode])
        self._mode.changed.connect(lambda _v: self._refresh_when())
        col.addWidget(self._mode)
        self._mode_help = _label("", "Muted", wrap=True)
        col.addWidget(self._mode_help)
        col.addSpacing(12)

        panel = QFrame()
        panel.setObjectName("Panel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(20, 8, 20, 8)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(0)

        def spin(minimum: int, maximum: int, step: int, suffix: str, name: str) -> QSpinBox:
            box = QSpinBox()
            box.setRange(minimum, maximum)
            box.setSingleStep(step)
            box.setSuffix(suffix)
            box.setFixedWidth(150)
            box.setAlignment(Qt.AlignmentFlag.AlignRight)
            box.setAccessibleName(name)
            box.valueChanged.connect(lambda _v: self._refresh_when())
            return box

        self._confirm = spin(1, 50, 1, " quadros", "Quadros para confirmar")
        self._release = spin(1, 50, 1, " quadros", "Quadros para considerar que sumiu")
        self._delay = spin(0, 600_000, 100, " ms", "Espera antes de agir")
        self._cooldown = spin(0, 86_400_000, 100, " ms", "Intervalo mínimo entre execuções")
        self._limit = spin(0, 1_000_000, 1, "", "Limite de execuções")
        self._limit.setSpecialValueText("Sem limite")
        self._confirm_hint = _label("", "Faint", wrap=True)
        rows = [
            ("Confirmar a cor após", "Evita agir por um piscar de tela.", self._confirm, self._confirm_hint),
            ("Considerar que sumiu após", "Uma falha rápida na tela não conta como sumiço.", self._release, None),
            ("Esperar antes de agir", "Se a cor sumir durante a espera, a ação é cancelada.", self._delay, None),
            ("Intervalo mínimo entre execuções", "Nunca age duas vezes em menos tempo que isso.", self._cooldown, None),
            ("Limite de execuções", "Para de agir ao atingir este número. Zero significa sem limite.", self._limit, None),
        ]
        for index, (title, help_text, control, extra) in enumerate(rows):
            if index:
                line = QFrame()
                line.setObjectName("Divider")
                line.setFixedHeight(1)
                grid.addWidget(line, index * 2 - 1, 0, 1, 2)
            texts = QVBoxLayout()
            texts.setContentsMargins(0, 12, 0, 12)
            texts.setSpacing(2)
            texts.addWidget(_label(title))
            texts.addWidget(_label(help_text, "Faint", wrap=True))
            if extra is not None:
                texts.addWidget(extra)
            holder = QWidget()
            holder.setLayout(texts)
            grid.addWidget(holder, index * 2, 0)
            grid.addWidget(control, index * 2, 1, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        grid.setColumnStretch(0, 1)
        col.addWidget(panel)
        col.addStretch(1)
        return page

    # -- etapa 3: o que fazer ------------------------------------------------------------------
    def _build_do(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(10)
        col.addWidget(_label("Ação", "SectionTitle"))
        self._action_editor = ActionEditor(self._selected_zone)
        self._action_editor.changed.connect(self._refresh_summary)
        col.addWidget(self._action_editor)
        col.addSpacing(10)
        col.addWidget(_label("Resumo", "SectionTitle"))
        self._summary = WrapLabel("", "Muted")
        col.addWidget(self._summary)
        col.addStretch(1)
        return page

    # -- dados -----------------------------------------------------------------------------
    def _load(self) -> None:
        rule = self._rule
        trigger = rule.trigger if rule else TriggerSettings()
        if rule is not None:
            self._name.setText(rule.name)
            if rule.zone_id is not None:
                self._conditions[rule.zone_id] = rule.condition
        else:
            existing = {r.name for r in self._ctx.rules.list(self._profile_id)}
            number = 1
            while f"Automação {number:02d}" in existing:
                number += 1
            self._name.setText(f"Automação {number:02d}")
        if rule is not None:
            self._action_editor.set_action(rule.action)
        self._mode.set_value(trigger.mode.value)
        self._confirm.setValue(trigger.confirm_frames)
        self._release.setValue(trigger.release_frames)
        self._delay.setValue(trigger.delay_ms)
        self._cooldown.setValue(trigger.cooldown_ms)
        self._limit.setValue(trigger.max_executions)

        initial = next((z for z in self._zones if rule and z.id == rule.zone_id), None)
        if initial is None and self._zones and rule is None:
            initial = self._zones[0]
        if initial is not None:
            self._zone_buttons[initial.id].setChecked(True)
            self._zone_selected(initial)
        self._refresh_when()

    def _selected_zone(self) -> Zone | None:
        return next((z for z in self._zones if self._zone_buttons[z.id].isChecked()), None)

    def _zone_selected(self, zone: Zone) -> None:
        if zone.id not in self._conditions:
            self._conditions[zone.id] = self._default_condition(zone)
        self._refresh_condition()

    def _default_condition(self, zone: Zone) -> ColorCondition:
        draft = self._ctx.zones.color_draft(zone.id)
        if draft is not None:
            return draft  # o que o usuário já ajustou em "Testar cor"
        image = self._grab(zone)
        if image is None:
            return ColorCondition((255, 255, 255))
        return ColorCondition(suggest_color(image))

    def _condition(self) -> ColorCondition | None:
        zone = self._selected_zone()
        return self._conditions.get(zone.id) if zone else None

    def _trigger(self) -> TriggerSettings:
        return TriggerSettings(
            mode=FireMode(self._mode.value()),
            confirm_frames=self._confirm.value(),
            release_frames=self._release.value(),
            delay_ms=self._delay.value(),
            cooldown_ms=self._cooldown.value(),
            max_executions=self._limit.value(),
        )

    def _grab(self, zone: Zone) -> Image | None:
        try:
            return self._capturer.grab(zone.rect)
        except CaptureError:
            return None

    # -- atualizações de tela ------------------------------------------------------------------
    def _refresh_condition(self) -> None:
        condition = self._condition()
        if condition is None:
            self._swatch.setStyleSheet(f"background: {Color.BG}; border: 1px dashed {Color.LINE_STRONG}; border-radius: 10px;")
            self._condition_label.setText("Escolha uma zona")
            self._live_label.setText("")
            return
        self._swatch.setStyleSheet(
            f"background: {condition.hex}; border: 1px solid {Color.LINE_STRONG}; border-radius: 10px;"
        )
        self._condition_label.setText(condition.hex)
        self._refresh_live()

    def _refresh_live(self) -> None:
        zone, condition = self._selected_zone(), self._condition()
        if zone is None or condition is None or self._step != 0:
            return
        image = self._grab(zone)
        detail = condition_text(condition)
        if image is None:
            self._live_label.setText(f"{detail}. Não foi possível ver a zona agora.")
            return
        result = detect(image, condition)
        now = (
            f"Agora: encontrada em {result.ratio * 100:.1f}% da zona".replace(".", ",")
            if result.detected
            else "Agora: não encontrada"
        )
        self._live_label.setText(f"{detail}.\n{now}.")

    def _refresh_when(self) -> None:
        mode = FireMode(self._mode.value())
        self._mode_help.setText(MODE_HELP[mode])
        zone = self._selected_zone()
        if zone is not None:
            self._confirm_hint.setText(
                f"Nesta zona, {self._confirm.value()} quadros levam cerca de "
                f"{seconds(self._confirm.value() * zone.interval_ms)}."
            )
        if mode is FireMode.CONTINUOUS and self._cooldown.value() < MIN_CONTINUOUS_COOLDOWN_MS:
            self._cooldown.setValue(MIN_CONTINUOUS_COOLDOWN_MS)

    def _refresh_summary(self) -> None:
        from vanthor.ui.rule_text import summary, trigger_details

        zone, condition = self._selected_zone(), self._condition()
        if zone is None or condition is None:
            self._summary.setText("")
            return
        try:
            action = self._action_editor.action()
        except ValueError as exc:
            self._summary.setText(f"Falta configurar a ação: {exc}.")
            return
        try:
            trigger = self._trigger()
        except ValueError:
            return
        preview = Rule(0, self._profile_id, self._name.text(), RuleStatus.ACTIVE, zone.id, condition, trigger, action)
        self._summary.setText(f"{summary(preview, zone.name)}\n{trigger_details(preview.trigger)}.")

    # -- navegação ---------------------------------------------------------------------------
    def _validate_step(self, step: int) -> str | None:
        if step == 0:
            if not self._name.text().strip():
                return "Dê um nome para a automação."
            if self._selected_zone() is None:
                return "Escolha a zona que será observada." if self._zones else "Marque uma zona antes de criar a automação."
        if step == 1:
            try:
                self._trigger()
            except ValueError as exc:
                return f"{exc}."
        if step == 2:
            try:
                self._action_editor.action()
            except ValueError as exc:
                return f"{exc}."
        return None

    def _go(self, step: int) -> None:
        step = max(0, min(step, len(_STEPS) - 1))
        self._error.hide()
        if step > self._step:
            # Avançar (pelo botão ou pulando etapas no topo) exige que as etapas do caminho estejam ok.
            for current in range(self._step, step):
                problem = self._validate_step(current)
                if problem:
                    self._show_error(problem)
                    step = current
                    break
        self._step = step
        self._pages.setCurrentIndex(step)
        for index, button in enumerate(self._step_buttons):
            button.setChecked(index == step)
        self._back.setVisible(step > 0)
        if step == len(_STEPS) - 1:
            self._next.setText("  Salvar alterações" if self._rule else "  Criar automação")
            self._next.setIcon(icons.icon("check", Color.ON_BRASA, 16))
            self._refresh_summary()
        else:
            self._next.setText("Próximo")
            self._next.setIcon(QIcon())
        if step == 0:
            self._refresh_live()

    def _advance(self) -> None:
        if self._step < len(_STEPS) - 1:
            problem = self._validate_step(self._step)
            if problem:
                self._show_error(problem)
                return
            self._go(self._step + 1)
        else:
            self._save()

    def _show_error(self, text: str) -> None:
        self._error.setText(text)
        self._error.show()

    def _adjust_color(self) -> None:
        zone, condition = self._selected_zone(), self._condition()
        if zone is None:
            self._show_error("Escolha a zona primeiro.")
            return
        self._live.stop()
        dialog = ColorTestDialog(self._ctx, zone, self, initial=condition, save_draft=False)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._conditions[zone.id] = dialog.condition()
            self._refresh_condition()
        self._live.start()

    def _save(self) -> None:
        for step in range(len(_STEPS)):
            problem = self._validate_step(step)
            if problem:
                self._go(step)
                self._show_error(problem)
                return
        zone, condition = self._selected_zone(), self._condition()
        assert zone is not None and condition is not None
        try:
            if self._rule is None:
                self.saved_rule = self._ctx.rules.create(
                    self._profile_id, self._name.text(), zone.id, condition, self._trigger(), self._action_editor.action()
                )
            else:
                updated = self._rule.with_changes(
                    name=self._name.text().strip(),
                    zone_id=zone.id,
                    condition=condition,
                    trigger=self._trigger(),
                    action=self._action_editor.action(),
                    status=RuleStatus.ACTIVE if self._rule.status is RuleStatus.DISABLED else self._rule.status,
                )
                self._ctx.rules.update(updated)
                self.saved_rule = updated
        except ValueError as exc:
            self._show_error(f"{exc}.")
            return
        self.accept()

    def done(self, result: int) -> None:
        self._live.stop()
        self._capturer.close()
        super().done(result)
