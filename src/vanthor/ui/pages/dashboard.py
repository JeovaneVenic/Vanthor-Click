"""Painel: o estado do sistema de relance e o controle principal."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from vanthor.core.engine import EngineState
from vanthor.core.events import Event, EventKind
from vanthor.ui.context import AppContext
from vanthor.ui.copy import STATE_LABEL, format_count, format_duration, plural
from vanthor.ui.theme import icons
from vanthor.ui.theme.fonts import display_font
from vanthor.ui.theme.tokens import Color, Size
from vanthor.ui.widgets.activity_list import ActivityModel, ActivityRow, ActivityView
from vanthor.ui.widgets.status_eye import StatusEye


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _vline() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedWidth(1)
    return line


class _Stat(QWidget):
    def __init__(self, label: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(2)
        self.value = QLabel("0")
        self.value.setFont(display_font(Size.STAT, QFont.Weight.DemiBold))
        self.value.setAccessibleName(label)
        layout.addWidget(self.value)
        layout.addWidget(_label(label, "StatLabel"))


class DashboardPage(QWidget):
    navigate = Signal(str)
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._rules_total = 0
        self._rules_active = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(Size.PAGE_MARGIN, 28, Size.PAGE_MARGIN, Size.PAGE_MARGIN)
        root.setSpacing(20)

        header = QHBoxLayout()
        header.addWidget(_label("Painel", "PageTitle"))
        header.addStretch(1)
        self._profile = _label("", "Faint")
        header.addWidget(self._profile)
        root.addLayout(header)

        root.addWidget(self._build_banner())
        root.addWidget(self._build_hero())
        root.addWidget(self._build_stats())

        bottom = QHBoxLayout()
        bottom.setSpacing(20)
        bottom.addWidget(self._build_activity(), 3)
        self._onboarding = self._build_onboarding()
        bottom.addWidget(self._onboarding, 2)
        root.addLayout(bottom, 1)

        self._ticker = QTimer(self, interval=500)
        self._ticker.timeout.connect(self._refresh_uptime)

        ctx.session.state_changed.connect(self._on_state)
        ctx.session.event_received.connect(self._on_event)
        ctx.session.start_blocked.connect(self._show_banner)

        self._load_activity()
        self.refresh()

    # -- montagem ---------------------------------------------------------------------
    def _build_banner(self) -> QWidget:
        self._banner = QFrame()
        self._banner.setObjectName("Banner")
        layout = QHBoxLayout(self._banner)
        layout.setContentsMargins(14, 10, 8, 10)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(icons.pixmap("circle-alert", Color.DANGER, 18))
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        self._banner_text = _label("", "BannerText", wrap=True)
        layout.addWidget(self._banner_text, 1)
        close = QPushButton("Entendi")
        close.setObjectName("Quiet")
        close.clicked.connect(self._banner.hide)
        layout.addWidget(close)
        self._banner.hide()
        return self._banner

    def _build_hero(self) -> QWidget:
        self._hero = QFrame()
        self._hero.setObjectName("Hero")
        layout = QHBoxLayout(self._hero)
        layout.setContentsMargins(20, 18, 24, 18)
        layout.setSpacing(18)

        self._eye = StatusEye()
        layout.addWidget(self._eye)

        texts = QVBoxLayout()
        texts.setSpacing(4)
        texts.addStretch(1)
        self._state_title = _label("", "StateTitle")
        self._state_detail = _label("", "Muted", wrap=True)
        texts.addWidget(self._state_title)
        texts.addWidget(self._state_detail)
        texts.addStretch(1)
        layout.addLayout(texts, 1)

        self._pause = QPushButton()
        self._pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pause.setIconSize(QSize(16, 16))
        self._pause.setMinimumHeight(Size.PRIMARY_H)
        self._pause.clicked.connect(self._ctx.session.toggle_pause)
        layout.addWidget(self._pause)

        # Modo de teste: roda as automações de verdade, mas sem clicar nem digitar.
        self._test = QPushButton("  Testar sem clicar")
        self._test.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test.setIcon(icons.icon("eye", Color.SIM, 16))
        self._test.setIconSize(QSize(16, 16))
        self._test.setMinimumHeight(Size.PRIMARY_H)
        self._test.setToolTip("Mostra na tela onde cada automação clicaria, sem mexer no mouse nem no teclado")
        self._test.clicked.connect(self._start_test)
        layout.addWidget(self._test)

        self._main = QPushButton()
        self._main.setCursor(Qt.CursorShape.PointingHandCursor)
        self._main.setIconSize(QSize(18, 18))
        self._main.setMinimumWidth(148)
        self._main.clicked.connect(self._on_main_clicked)
        layout.addWidget(self._main)
        return self._hero

    def _build_stats(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)
        self._stat_uptime = _Stat("Tempo vigiando")
        self._stat_rules = _Stat("Automações")
        self._stat_active = _Stat("Ativas")
        self._stat_detections = _Stat("Detecções")
        self._stat_clicks = _Stat("Cliques")
        stats = [self._stat_uptime, self._stat_rules, self._stat_active, self._stat_detections, self._stat_clicks]
        for index, stat in enumerate(stats):
            if index:
                layout.addWidget(_vline())
            layout.addWidget(stat, 1)
        return panel

    def _build_activity(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(_label("Atividade recente", "SectionTitle"))

        self._activity_stack = QStackedLayout()
        self._activity_model = ActivityModel()
        self._activity_view = ActivityView(self._activity_model)
        empty = _label(
            "Nada aconteceu ainda. Quando você iniciar, cada início, pausa, detecção e clique aparece aqui.",
            "Muted",
            wrap=True,
        )
        empty.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._activity_stack.addWidget(empty)
        self._activity_stack.addWidget(self._activity_view)
        holder = QWidget()
        holder.setLayout(self._activity_stack)
        layout.addWidget(holder, 1)
        return panel

    def _build_onboarding(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        layout.addWidget(_label("Como uma automação funciona", "SectionTitle"))
        steps = (
            ("Observar", "Escolha uma área da tela e a cor que importa."),
            ("Decidir", "Defina quanto da cor precisa aparecer e por quanto tempo."),
            ("Agir", "Escolha onde clicar ou qual tecla pressionar, em qualquer ponto da tela."),
        )
        for number, (title, text) in enumerate(steps, start=1):
            row = QHBoxLayout()
            row.setSpacing(12)
            badge = _label(str(number), "StepNumber")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(2)
            heading = _label(title)
            heading.setStyleSheet("font-weight: 600;")
            col.addWidget(heading)
            col.addWidget(_label(text, "Muted", wrap=True))
            row.addLayout(col, 1)
            layout.addLayout(row)
        layout.addStretch(1)
        create = QPushButton("  Criar automação")
        create.setObjectName("Primary")
        create.setIcon(icons.icon("plus", Color.ON_BRASA, 18))
        create.setCursor(Qt.CursorShape.PointingHandCursor)
        create.clicked.connect(lambda: self.navigate.emit("rules"))
        layout.addWidget(create)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        return panel

    # -- dados ------------------------------------------------------------------------
    def refresh(self) -> None:
        profile = self._ctx.profiles.active()
        totals = self._ctx.rules.totals(profile.id)
        self._rules_total, self._rules_active = totals.rules, totals.active
        self._profile.setText(f"Perfil: {profile.name}")
        self._stat_rules.value.setText(format_count(totals.rules))
        self._stat_active.value.setText(format_count(totals.active))
        self._stat_detections.value.setText(format_count(totals.detections))
        self._stat_clicks.value.setText(format_count(totals.clicks))
        self._onboarding.setVisible(totals.rules == 0)
        self._on_state(self._ctx.session.state)

    def _load_activity(self) -> None:
        rows = [ActivityRow(e.timestamp, e.kind, e.level, e.message) for e in self._ctx.activity.recent(200)]
        self._activity_model.replace(rows)
        self._activity_stack.setCurrentIndex(1 if rows else 0)

    def _refresh_uptime(self) -> None:
        self._stat_uptime.value.setText(format_duration(self._ctx.engine.snapshot().active_seconds))

    # -- reações ----------------------------------------------------------------------
    def _on_main_clicked(self) -> None:
        if self._ctx.session.state is EngineState.STOPPED:
            if self._ctx.session.start():
                self._banner.hide()
        else:
            self._ctx.session.stop()

    def _start_test(self) -> None:
        if self._ctx.session.start(dry_run=True):
            self._banner.hide()

    def _show_banner(self, message: str) -> None:
        self._banner_text.setText(message)
        self._banner.show()

    def _on_event(self, event: Event) -> None:
        self._activity_model.prepend(ActivityRow(event.timestamp, event.kind, event.level, event.message))
        self._activity_stack.setCurrentIndex(1)
        self._activity_view.scrollToTop()
        if event.kind in (EventKind.DETECTION, EventKind.ACTION, EventKind.SIMULATED_ACTION) and self.isVisible():
            self.refresh()  # contadores de detecções e cliques ao vivo

    def _on_state(self, state: EngineState) -> None:
        hotkey = self._ctx.session.emergency_label()
        testing = self._ctx.session.dry_run and state is EngineState.RUNNING
        self._eye.set_state(state, testing=testing)
        self._state_title.setText("Modo de teste" if testing else STATE_LABEL[state])
        self._state_title.setStyleSheet(f"color: {Color.SIM};" if testing else "")
        self._hero.setProperty("state", "testing" if testing else state.value)
        self._hero.style().unpolish(self._hero)
        self._hero.style().polish(self._hero)

        if state is EngineState.STOPPED:
            self._state_detail.setText(
                "Nenhuma automação criada ainda."
                if self._rules_total == 0
                else f"{plural(self._rules_active, 'automação ativa pronta', 'automações ativas prontas')} para vigiar."
            )
            self._main.setObjectName("Primary")
            self._main.setText("  Iniciar")
            self._main.setIcon(icons.icon("play", Color.ON_BRASA, 18))
            self._main.setToolTip("Começa a vigiar a tela com as automações ativas")
            self._pause.hide()
            self._test.show()
            self._test.setEnabled(self._rules_active > 0)
            self._ticker.stop()
        else:
            watching = (
                f"Vigiando {plural(self._rules_active, 'automação', 'automações')}."
                if self._rules_active
                else "Nenhuma automação ativa para vigiar."
            )
            if testing:
                self._state_detail.setText(
                    f"Testando {plural(self._rules_active, 'automação', 'automações')} sem clicar nem digitar. "
                    "Um alvo ciano mostra onde cada clique aconteceria."
                )
            else:
                self._state_detail.setText(
                    f"{watching} {hotkey} para tudo na hora."
                    if state is EngineState.RUNNING
                    else f"Nada é verificado nem clicado enquanto estiver pausado. {hotkey} para tudo."
                )
            self._test.hide()
            self._main.setObjectName("StopPrimary")
            self._main.setText("  Parar")
            self._main.setIcon(icons.icon("square", Color.BG, 16))
            self._main.setToolTip(f"Para todas as automações (atalho: {hotkey})")
            self._pause.show()
            paused = state is EngineState.PAUSED
            self._pause.setText("  Retomar" if paused else "  Pausar")
            self._pause.setIcon(icons.icon("play" if paused else "pause", Color.TEXT, 16))
            self._ticker.start()
        self._main.style().unpolish(self._main)
        self._main.style().polish(self._main)
        self._refresh_uptime()
