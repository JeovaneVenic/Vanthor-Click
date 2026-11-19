"""Configurador da etapa "O que fazer?": clicar, pressionar teclas, sequência ou só registrar."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vanthor.actions.models import (
    MAX_STEPS,
    ActionKind,
    ActionSpec,
    ClickStep,
    KeyStep,
    MouseButton,
    Step,
    WaitStep,
)
from vanthor.capture.geometry import ScreenRect, monitor_for_point
from vanthor.capture.zones import Zone
from vanthor.ui.conceal import conceal_app_windows
from vanthor.ui.overlays.eyedropper import Eyedropper, Marker
from vanthor.ui.screens import monitor_maps
from vanthor.ui.theme import icons
from vanthor.ui.theme.tokens import Color
from vanthor.ui.widgets.segmented import Segmented
from vanthor.ui.widgets.toggle_switch import ToggleSwitch
from vanthor.winsys.hotkeys import taken_by_another_program
from vanthor.winsys.keys import parse_combo

log = logging.getLogger(__name__)

_KINDS: tuple[tuple[ActionKind, str, str, str], ...] = (
    (ActionKind.CLICK, "Clicar", "mouse-pointer-click", "Um clique num ponto da tela"),
    (ActionKind.KEYS, "Pressionar teclas", "keyboard", "Uma tecla ou combinação"),
    (ActionKind.SEQUENCE, "Sequência", "list-ordered", "Vários cliques, teclas e esperas"),
    (ActionKind.LOG, "Só registrar", "notebook-pen", "Anota no histórico, sem mexer em nada"),
)
_BUTTONS = [(MouseButton.LEFT.value, "Esquerdo"), (MouseButton.RIGHT.value, "Direito"), (MouseButton.MIDDLE.value, "Meio")]


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _point_text(x: int, y: int) -> str:
    match = monitor_for_point(x, y, monitor_maps())
    where = match.label if match else "fora das telas atuais"
    return f"X {x}   Y {y}   ({where})"


class _KeyEdit(QKeySequenceEdit):
    """Captura UMA combinação e já a converte para o formato do Vanthor."""

    combo_changed = Signal(str)

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self.setMaximumSequenceLength(1)
        self.setFixedWidth(200)
        self.setAccessibleName("Teclas")
        if initial:
            self.setKeySequence(QKeySequence(initial))
        self.editingFinished.connect(
            lambda: self.combo_changed.emit(self.keySequence().toString(QKeySequence.SequenceFormat.PortableText))
        )


class ActionEditor(QWidget):
    changed = Signal()

    def __init__(self, zone_provider: Callable[[], Zone | None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zone_provider = zone_provider
        self._kind = ActionKind.CLICK
        self._click: ClickStep | None = None
        self._keys = ""
        self._sequence: list[Step] = []
        self._picker: Eyedropper | None = None
        self._restore: Callable[[], None] | None = None

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(12)

        kinds = QHBoxLayout()
        kinds.setSpacing(10)
        self._kind_group = QButtonGroup(self)
        self._kind_buttons: dict[ActionKind, QToolButton] = {}
        for kind, title, icon_name, hint in _KINDS:
            button = QToolButton()
            button.setObjectName("ActionChoice")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setText(f"{title}\n{hint}")
            button.setIcon(icons.icon(icon_name, Color.TEXT, 22))
            button.setIconSize(QSize(22, 22))
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.clicked.connect(lambda _=False, k=kind: self._set_kind(k))
            self._kind_group.addButton(button)
            kinds.addWidget(button, 1)
            self._kind_buttons[kind] = button
        col.addLayout(kinds)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_click())
        self._pages.addWidget(self._build_keys())
        self._pages.addWidget(self._build_sequence())
        self._pages.addWidget(self._build_log())
        col.addWidget(self._pages)

        restore_row = QHBoxLayout()
        self._restore_cursor = ToggleSwitch()
        self._restore_cursor.setChecked(True)
        self._restore_cursor.setAccessibleName("Devolver o mouse para onde estava")
        self._restore_cursor.toggled.connect(lambda _on: self.changed.emit())
        restore_row.addWidget(self._restore_cursor)
        restore_row.addWidget(_label("Devolver o mouse para onde estava depois de clicar"))
        restore_row.addStretch(1)
        self._restore_host = QWidget()
        self._restore_host.setLayout(restore_row)
        col.addWidget(self._restore_host)

        self._set_kind(ActionKind.CLICK)

    # -- páginas ------------------------------------------------------------------------------
    def _panel(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        return frame, layout

    def _build_click(self) -> QWidget:
        frame, layout = self._panel()
        row = QHBoxLayout()
        row.setSpacing(12)
        target = QLabel()
        target.setPixmap(icons.pixmap("target", Color.BRASA, 22))
        row.addWidget(target)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(_label("Ponto do clique"))
        self._click_point = _label("", "Muted")
        texts.addWidget(self._click_point)
        row.addLayout(texts, 1)
        pick = QPushButton("  Selecionar ponto na tela")
        pick.setObjectName("Primary")
        pick.setIcon(icons.icon("crosshair", Color.ON_BRASA, 16))
        pick.setCursor(Qt.CursorShape.PointingHandCursor)
        pick.clicked.connect(lambda: self.pick_point(self._set_click_point))
        row.addWidget(pick)
        layout.addLayout(row)

        options = QHBoxLayout()
        options.setSpacing(12)
        options.addWidget(_label("Botão", "Muted"))
        self._click_button = Segmented(_BUTTONS)
        self._click_button.set_value(MouseButton.LEFT.value)
        self._click_button.changed.connect(lambda _v: self._click_options_changed())
        options.addWidget(self._click_button)
        options.addSpacing(16)
        self._click_double = ToggleSwitch()
        self._click_double.setAccessibleName("Clique duplo")
        self._click_double.toggled.connect(lambda _on: self._click_options_changed())
        options.addWidget(self._click_double)
        options.addWidget(_label("Clique duplo"))
        options.addStretch(1)
        layout.addLayout(options)
        layout.addWidget(
            _label(
                "O ponto do clique pode ficar em qualquer lugar da tela, não precisa estar dentro da zona.",
                "Faint",
                wrap=True,
            )
        )
        return frame

    def _build_keys(self) -> QWidget:
        frame, layout = self._panel()
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(_label("Teclas"))
        self._keys_edit = _KeyEdit()
        self._keys_edit.combo_changed.connect(self._set_keys)
        row.addWidget(self._keys_edit)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(
            _label(
                "Clique no campo e pressione a tecla ou a combinação. Exemplos: Enter, F5, Ctrl+S. "
                "As teclas vão para o programa que estiver em primeiro plano.",
                "Faint",
                wrap=True,
            )
        )
        self._keys_warning = _label("", "FieldError", wrap=True)
        self._keys_warning.hide()
        layout.addWidget(self._keys_warning)
        return frame

    def _build_sequence(self) -> QWidget:
        frame, layout = self._panel()
        self._steps_host = QVBoxLayout()
        self._steps_host.setSpacing(8)
        layout.addLayout(self._steps_host)
        self._steps_empty = _label("Adicione os passos na ordem em que devem acontecer.", "Muted")
        layout.addWidget(self._steps_empty)
        adds = QHBoxLayout()
        adds.setSpacing(8)
        for text, icon_name, handler in (
            ("Clique", "mouse-pointer-click", self._add_click_step),
            ("Teclas", "keyboard", lambda: self._append(KeyStep("Enter"))),
            ("Espera", "timer", lambda: self._append(WaitStep(300))),
        ):
            button = QPushButton(f"  {text}")
            button.setIcon(icons.icon(icon_name, Color.TEXT, 16))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"Adicionar {text.lower()} ao fim da sequência")
            button.clicked.connect(handler)
            adds.addWidget(button)
        adds.addStretch(1)
        layout.addLayout(adds)
        return frame

    def _build_log(self) -> QWidget:
        frame, layout = self._panel()
        layout.addWidget(
            _label(
                "Anota no histórico cada vez que a automação dispara, sem mexer no mouse nem no teclado. "
                "Use para conferir se a automação reage na hora certa antes de deixá-la clicar.",
                "Muted",
                wrap=True,
            )
        )
        return frame

    # -- estado ---------------------------------------------------------------------------------
    def set_action(self, action: ActionSpec) -> None:
        self._kind = action.kind
        if action.kind is ActionKind.CLICK and isinstance(action.steps[0], ClickStep):
            self._click = action.steps[0]
            self._click_button.set_value(self._click.button.value)
            self._click_double.setChecked(self._click.double)
        if action.kind is ActionKind.KEYS and isinstance(action.steps[0], KeyStep):
            self._keys = action.steps[0].combo
            self._keys_edit.setKeySequence(QKeySequence(self._keys))
            self._warn_if_taken()
        if action.kind is ActionKind.SEQUENCE:
            self._sequence = list(action.steps)
        self._restore_cursor.setChecked(action.restore_cursor)
        self._set_kind(action.kind, emit=False)
        self._render_click()
        self._render_sequence()

    def action(self) -> ActionSpec:
        """A ação configurada. ``ValueError`` com mensagem amigável se faltar algo."""
        restore = self._restore_cursor.isChecked()
        if self._kind is ActionKind.CLICK:
            if self._click is None:
                raise ValueError("Escolha o ponto do clique na tela")
            return ActionSpec(ActionKind.CLICK, (self._click,), restore)
        if self._kind is ActionKind.KEYS:
            if not self._keys:
                raise ValueError("Escolha a tecla ou combinação")
            return ActionSpec(ActionKind.KEYS, (KeyStep(self._keys),), restore)
        if self._kind is ActionKind.SEQUENCE:
            return ActionSpec(ActionKind.SEQUENCE, tuple(self._sequence), restore)
        return ActionSpec(ActionKind.LOG)

    def _set_kind(self, kind: ActionKind, emit: bool = True) -> None:
        self._kind = kind
        self._kind_buttons[kind].setChecked(True)
        index = [k for k, *_ in _KINDS].index(kind)
        # O QStackedWidget reserva a altura da página mais alta; ignorar o tamanho das páginas ocultas
        # faz o painel ter só a altura da opção escolhida (sem buracos entre os campos).
        for i in range(self._pages.count()):
            policy = QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored
            self._pages.widget(i).setSizePolicy(QSizePolicy.Policy.Preferred, policy)
        self._pages.setCurrentIndex(index)
        self._pages.adjustSize()
        self._restore_host.setVisible(kind in (ActionKind.CLICK, ActionKind.SEQUENCE))
        if emit:
            self.changed.emit()

    # -- clique ----------------------------------------------------------------------------------
    def _set_click_point(self, x: int, y: int) -> None:
        button = MouseButton(self._click_button.value())
        self._click = ClickStep(x, y, button, self._click_double.isChecked())
        self._render_click()
        self.changed.emit()

    def _click_options_changed(self) -> None:
        if self._click is not None:
            self._click = ClickStep(
                self._click.x, self._click.y, MouseButton(self._click_button.value()), self._click_double.isChecked()
            )
        self.changed.emit()

    def _render_click(self) -> None:
        self._click_point.setText(
            _point_text(self._click.x, self._click.y) if self._click else "Nenhum ponto escolhido ainda"
        )

    # -- teclas ----------------------------------------------------------------------------------
    def _set_keys(self, text: str) -> None:
        if not text:
            return
        try:
            self._keys = KeyStep(text).combo
        except ValueError as exc:
            log.info("Tecla recusada: %s", exc)
            self._keys = ""
            self._keys_edit.clear()
        self._warn_if_taken()
        self.changed.emit()

    def _warn_if_taken(self) -> None:
        taken = bool(self._keys) and taken_by_another_program(parse_combo(self._keys))
        self._keys_warning.setText(
            f"{self._keys} é um atalho global de outro programa aberto. O Windows entrega essas teclas a ele, "
            "então elas podem não chegar ao destino. Prefira outra combinação."
            if taken
            else ""
        )
        self._keys_warning.setVisible(taken)

    # -- sequência --------------------------------------------------------------------------------
    def _add_click_step(self) -> None:
        self.pick_point(lambda x, y: self._append(ClickStep(x, y)))

    def _append(self, step: Step) -> None:
        if len(self._sequence) >= MAX_STEPS:
            return
        self._sequence.append(step)
        self._render_sequence()
        self.changed.emit()

    def _replace(self, index: int, step: Step, rerender: bool = False) -> None:
        self._sequence[index] = step
        if rerender:
            self._render_sequence()
        self.changed.emit()

    def _move(self, index: int, delta: int) -> None:
        target = index + delta
        if 0 <= target < len(self._sequence):
            self._sequence[index], self._sequence[target] = self._sequence[target], self._sequence[index]
            self._render_sequence()
            self.changed.emit()

    def _remove(self, index: int) -> None:
        del self._sequence[index]
        self._render_sequence()
        self.changed.emit()

    def _render_sequence(self) -> None:
        while self._steps_host.count():
            item = self._steps_host.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for index, step in enumerate(self._sequence):
            self._steps_host.addWidget(self._step_row(index, step))
        self._steps_empty.setVisible(not self._sequence)

    def _step_row(self, index: int, step: Step) -> QWidget:
        row_frame = QFrame()
        row_frame.setObjectName("StepRow")
        row = QHBoxLayout(row_frame)
        row.setContentsMargins(10, 6, 6, 6)
        row.setSpacing(10)
        number = _label(str(index + 1), "StepNumber")
        number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(number)

        if isinstance(step, ClickStep):
            row.addWidget(_label(f"Clique em {step.x}, {step.y}"), 1)
            buttons = Segmented(_BUTTONS)
            buttons.set_value(step.button.value)
            double = ToggleSwitch()
            double.setChecked(step.double)
            double.setToolTip("Clique duplo")

            def update_click(_v: object = None, i: int = index, b: Segmented = buttons, d: ToggleSwitch = double) -> None:
                current = self._sequence[i]
                if isinstance(current, ClickStep):
                    self._replace(i, ClickStep(current.x, current.y, MouseButton(b.value()), d.isChecked()))

            buttons.changed.connect(update_click)
            double.toggled.connect(update_click)
            row.addWidget(buttons)
            row.addWidget(double)
            row.addWidget(_label("Duplo", "Faint"))
            repick = QPushButton("Mudar ponto")
            repick.setCursor(Qt.CursorShape.PointingHandCursor)
            repick.clicked.connect(
                lambda _=False, i=index: self.pick_point(
                    lambda x, y: self._replace(i, self._with_point(i, x, y), rerender=True)
                )
            )
            row.addWidget(repick)
        elif isinstance(step, KeyStep):
            row.addWidget(_label("Pressionar"))
            edit = _KeyEdit(step.combo)

            def update_keys(text: str, i: int = index, e: _KeyEdit = edit) -> None:
                try:
                    self._replace(i, KeyStep(text))
                except ValueError:
                    current = self._sequence[i]
                    e.setKeySequence(QKeySequence(current.combo if isinstance(current, KeyStep) else ""))

            edit.combo_changed.connect(update_keys)
            row.addWidget(edit)
            row.addStretch(1)
        else:
            row.addWidget(_label("Esperar"))
            spin = QSpinBox()
            spin.setRange(1, 60_000)
            spin.setSingleStep(100)
            spin.setSuffix(" ms")
            spin.setValue(step.ms)
            spin.setFixedWidth(130)
            spin.valueChanged.connect(lambda v, i=index: self._replace(i, WaitStep(v)))
            row.addWidget(spin)
            row.addStretch(1)

        for icon_name, tip, handler in (
            ("chevron-up", "Mover para cima", lambda _=False, i=index: self._move(i, -1)),
            ("chevron-down", "Mover para baixo", lambda _=False, i=index: self._move(i, 1)),
            ("x", "Remover passo", lambda _=False, i=index: self._remove(i)),
        ):
            button = QPushButton()
            button.setObjectName("Quiet")
            button.setIcon(icons.icon(icon_name, Color.TEXT_2, 16))
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.setFixedWidth(36)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(handler)
            row.addWidget(button)
        return row_frame

    def _with_point(self, index: int, x: int, y: int) -> ClickStep:
        current = self._sequence[index]
        if isinstance(current, ClickStep):
            return ClickStep(x, y, current.button, current.double)
        return ClickStep(x, y)

    # -- seletor de ponto --------------------------------------------------------------------------
    def markers(self) -> tuple[Marker, ...]:
        found: list[Marker] = []
        zone = self._zone_provider()
        if zone is not None:
            found.append(Marker(zone.rect, zone.color, f"Zona observada: {zone.name}"))
        points: list[ClickStep] = []
        if self._kind is ActionKind.CLICK and self._click:
            points.append(self._click)
        if self._kind is ActionKind.SEQUENCE:
            points += [s for s in self._sequence if isinstance(s, ClickStep)]
        for index, point in enumerate(points, start=1):
            label = "Clique atual" if self._kind is ActionKind.CLICK else f"Passo {index}"
            found.append(Marker(ScreenRect(point.x, point.y, 1, 1), Color.BRASA, label))
        return tuple(found)

    def pick_point(self, on_pick: Callable[[int, int], None]) -> None:
        if self._picker is not None:
            return
        picker = Eyedropper(
            title="Ponto do clique",
            hint="Clique exatamente onde o Vanthor deve clicar. Esc cancela.",
            markers=self.markers(),
        )

        def picked(_rgb: object, x: int, y: int) -> None:
            self._finish_pick()
            on_pick(x, y)

        picker.picked.connect(picked)
        picker.cancelled.connect(self._finish_pick)
        self._picker = picker
        self._restore = conceal_app_windows()
        QTimer.singleShot(120, picker.start)

    def _finish_pick(self) -> None:
        self._picker = None
        if self._restore is not None:
            self._restore()
            self._restore = None
