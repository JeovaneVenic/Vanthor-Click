"""Configurações: só opções que já funcionam de verdade nesta versão."""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vanthor import __version__
from vanthor.core.engine import EngineState
from vanthor.persistence.repositories import MAX_CLICKS_PER_MINUTE_RANGE
from vanthor.ui.context import AppContext
from vanthor.ui.theme import icons
from vanthor.ui.theme.tokens import Color, Size
from vanthor.ui.widgets.toggle_switch import ToggleSwitch
from vanthor.winsys import autostart
from vanthor.winsys.hotkeys import HotkeyError, parse_hotkey
from vanthor.winsys.paths import data_dir

log = logging.getLogger(__name__)


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


class _Section(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("Panel")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(24, 18, 24, 6)
        self._layout.setSpacing(0)
        self._layout.addWidget(_label(title, "SectionTitle"))
        self._layout.addSpacing(6)
        self._rows = 0

    def add_row(self, title: str, description: str, control: QWidget, below: QWidget | None = None) -> None:
        if self._rows:
            line = QFrame()
            line.setObjectName("Divider")
            line.setFixedHeight(1)
            self._layout.addWidget(line)
        row = QHBoxLayout()
        row.setContentsMargins(0, 14, 0, 14)
        row.setSpacing(24)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addWidget(_label(title))
        texts.addWidget(_label(description, "Muted", wrap=True))
        if below is not None:
            texts.addWidget(below)
        row.addLayout(texts, 1)
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        holder = QWidget()
        holder.setLayout(row)
        self._layout.addWidget(holder)
        self._rows += 1


class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(Size.PAGE_MARGIN, 28, Size.PAGE_MARGIN, Size.PAGE_MARGIN)
        root.setSpacing(20)
        root.addWidget(_label("Configurações", "PageTitle"))
        root.addWidget(self._security_section())
        root.addWidget(self._limits_section())
        root.addWidget(self._system_section())
        root.addWidget(self._about_section())
        root.addStretch(1)

        ctx.session.state_changed.connect(self._on_state)
        self._on_state(ctx.session.state)

    # -- segurança --------------------------------------------------------------------
    def _security_section(self) -> QWidget:
        section = _Section("Segurança")
        control = QWidget()
        layout = QHBoxLayout(control)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._hotkey_edit = QKeySequenceEdit()
        self._hotkey_edit.setMaximumSequenceLength(1)
        self._hotkey_edit.setFixedWidth(180)
        self._hotkey_edit.setKeySequence(QKeySequence(self._ctx.settings.load().emergency_hotkey))
        self._hotkey_edit.editingFinished.connect(self._save_hotkey)
        self._hotkey_edit.setAccessibleName("Tecla de emergência")
        layout.addWidget(self._hotkey_edit)

        self._reset_hotkey = QPushButton("Usar Esc")
        self._reset_hotkey.clicked.connect(lambda: self._apply_hotkey("Esc"))
        layout.addWidget(self._reset_hotkey)

        self._hotkey_feedback = _label("", "Faint", wrap=True)
        self._hotkey_feedback.hide()
        section.add_row(
            "Tecla de emergência",
            "Para todas as automações na hora, mesmo com outro programa em primeiro plano. "
            "Clique no campo e pressione a tecla ou combinação.",
            control,
            self._hotkey_feedback,
        )
        return section

    def _save_hotkey(self) -> None:
        text = self._hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if text:
            self._apply_hotkey(text)

    def _apply_hotkey(self, text: str) -> None:
        settings = self._ctx.settings.load()
        try:
            hotkey = parse_hotkey(text)
        except HotkeyError as exc:
            self._feedback(f"{exc}. A tecla anterior ({settings.emergency_hotkey}) continua valendo.", error=True)
            self._hotkey_edit.setKeySequence(QKeySequence(settings.emergency_hotkey))
            return
        settings.emergency_hotkey = hotkey.label
        self._ctx.settings.save(settings)
        self._hotkey_edit.setKeySequence(QKeySequence(hotkey.label))
        self._feedback(f"Tecla de emergência salva: {hotkey.label}.", error=False)
        self._on_state(self._ctx.session.state)  # atualiza textos que citam a tecla

    def _feedback(self, text: str, *, error: bool) -> None:
        self._hotkey_feedback.setObjectName("FieldError" if error else "Faint")
        self._hotkey_feedback.style().unpolish(self._hotkey_feedback)
        self._hotkey_feedback.style().polish(self._hotkey_feedback)
        self._hotkey_feedback.setText(text)
        self._hotkey_feedback.show()

    # -- sistema ----------------------------------------------------------------------
    # -- limites e avisos --------------------------------------------------------------------
    def _limits_section(self) -> QWidget:
        section = _Section("Limites e avisos")
        settings = self._ctx.settings.load()

        self._click_limit = QSpinBox()
        low, high = MAX_CLICKS_PER_MINUTE_RANGE
        self._click_limit.setRange(low, high)
        self._click_limit.setSuffix(" por minuto")
        self._click_limit.setFixedWidth(170)
        self._click_limit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._click_limit.setAccessibleName("Limite de cliques por minuto")
        self._click_limit.setValue(settings.max_clicks_per_minute)
        self._click_limit.valueChanged.connect(lambda v: self._update(max_clicks_per_minute=int(v)))
        section.add_row(
            "Limite de cliques",
            "Soma de todas as automações. Ao atingir o limite, as ações seguintes são ignoradas até o minuto "
            "liberar, e o histórico avisa. Vale a partir da próxima vez que você iniciar.",
            self._click_limit,
        )

        indicator = ToggleSwitch()
        indicator.setChecked(settings.show_indicator)
        indicator.setAccessibleName("Mostrar indicador na tela")
        indicator.toggled.connect(lambda on: self._update(show_indicator=bool(on)))
        section.add_row(
            "Indicador na tela",
            "Uma pílula no topo da tela mostra que há automação rodando, com o botão Parar. "
            "Aparece mesmo com o Vanthor minimizado. Você pode arrastá-la.",
            indicator,
        )

        background = ToggleSwitch()
        background.setChecked(settings.run_in_background)
        background.setAccessibleName("Continuar em segundo plano ao fechar")
        background.toggled.connect(lambda on: self._update(run_in_background=bool(on)))
        section.add_row(
            "Continuar em segundo plano",
            "Fechar a janela deixa o Vanthor no ícone da bandeja, perto do relógio, com as automações rodando. "
            "Para encerrar de vez, use Sair no menu desse ícone.",
            background,
        )

        notifications = ToggleSwitch()
        notifications.setChecked(settings.notifications)
        notifications.setAccessibleName("Notificações do Windows")
        notifications.toggled.connect(lambda on: self._update(notifications=bool(on)))
        section.add_row(
            "Notificações do Windows",
            "Avisa na bandeja quando houver parada de emergência ou erro e o Vanthor não estiver em primeiro plano.",
            notifications,
        )
        return section

    def _update(self, **changes: object) -> None:
        settings = self._ctx.settings.load()
        for key, value in changes.items():
            setattr(settings, key, value)
        try:
            settings.__post_init__()
        except ValueError:
            log.warning("Configuração recusada: %s", changes)
            return
        self._ctx.settings.save(settings)

    def _system_section(self) -> QWidget:
        section = _Section("Sistema")
        self._autostart = ToggleSwitch()
        self._autostart.setAccessibleName("Iniciar com o Windows")
        self._autostart.setChecked(autostart.is_enabled())
        self._autostart.toggled.connect(self._set_autostart)
        self._autostart_error = _label("", "FieldError", wrap=True)
        self._autostart_error.hide()
        section.add_row(
            "Iniciar com o Windows",
            "Abre em segundo plano, no ícone da bandeja, quando você entra no Windows. "
            "As automações continuam paradas até você iniciar.",
            self._autostart,
            self._autostart_error,
        )

        open_folder = QPushButton("  Abrir pasta")
        open_folder.setIcon(icons.icon("folder-open", Color.TEXT, 16))
        open_folder.setIconSize(QSize(16, 16))
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir()))))
        path = _label(str(data_dir()), "Faint", wrap=True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        section.add_row(
            "Pasta de dados",
            "Configurações, perfis e histórico ficam aqui e são mantidos ao atualizar o aplicativo.",
            open_folder,
            path,
        )
        return section

    def _set_autostart(self, enabled: bool) -> None:
        try:
            autostart.set_enabled(enabled)
        except OSError as exc:
            log.exception("Falha ao alterar a inicialização automática")
            self._autostart.setChecked(not enabled)
            self._autostart_error.setText(f"O Windows não permitiu a alteração: {exc}")
            self._autostart_error.show()
            return
        self._autostart_error.hide()
        settings = self._ctx.settings.load()
        settings.start_with_windows = enabled
        self._ctx.settings.save(settings)

    # -- sobre --------------------------------------------------------------------------
    def _about_section(self) -> QWidget:
        section = _Section("Sobre")
        version = _label(f"Versão {__version__}", "Muted")
        section.add_row(
            "Vanthor Bot Click",
            "Automação visual local. Nada sai do seu computador.",
            version,
        )
        return section

    # -- estado -------------------------------------------------------------------------
    def _on_state(self, state: EngineState) -> None:
        editable = state is EngineState.STOPPED
        self._hotkey_edit.setEnabled(editable)
        self._reset_hotkey.setEnabled(editable)
        self._hotkey_edit.setToolTip("" if editable else "Pare as automações para trocar a tecla de emergência.")
