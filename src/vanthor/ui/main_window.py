"""Janela principal: barra lateral + páginas, indicador de execução e a tecla de emergência."""

from __future__ import annotations

import logging
from ctypes import wintypes

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QCloseEvent, QIcon, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from vanthor import APP_NAME
from vanthor.core.engine import EngineState
from vanthor.ui.capture_guard import CaptureGuard
from vanthor.ui.context import AppContext
from vanthor.ui.pages.dashboard import DashboardPage
from vanthor.ui.overlays.click_flash import ClickFlashes
from vanthor.ui.overlays.run_indicator import RunIndicator
from vanthor.ui.pages.monitor import MonitorPage
from vanthor.ui.pages.profiles import ProfilesPage
from vanthor.ui.pages.rules import RulesPage
from vanthor.ui.tray import VanthorTray
from vanthor.ui.pages.settings import SettingsPage
from vanthor.ui.pages.zones import ZonesPage
from vanthor.ui.widgets.toast import Toast
from vanthor.ui.theme.tokens import Color
from vanthor.ui.widgets.sidebar import Sidebar
from vanthor.winsys.desktop import style_title_bar
from vanthor.winsys.hotkeys import WM_HOTKEY
from vanthor.winsys.paths import resource_path

log = logging.getLogger(__name__)

_WM_ENDSESSION = 0x0016

# As próximas fases acrescentam páginas aqui (Automações, Monitoramento, Perfis).
_PAGES: list[tuple[str, str, str]] = [
    ("dashboard", "Painel", "layout-dashboard"),
    ("rules", "Automações", "zap"),
    ("zones", "Zonas", "square-dashed"),
    ("monitor", "Monitoramento", "activity"),
    ("profiles", "Perfis", "layers"),
    ("settings", "Configurações", "settings"),
]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._styled = False
        self.setWindowIcon(QIcon(str(resource_path("brand", "app.ico"))))
        self.setMinimumSize(1000, 660)
        self.resize(1200, 780)

        root = QWidget()
        root.setObjectName("Root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._sidebar = Sidebar(_PAGES)
        layout.addWidget(self._sidebar)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        # Faixa fina sempre visível enquanto há automação: impossível esquecer que está rodando.
        self._run_strip = QFrame()
        self._run_strip.setObjectName("RunStrip")
        self._run_strip.setFixedHeight(3)
        column.addWidget(self._run_strip)

        self._stack = QStackedWidget()
        self._toast = Toast(root)
        self._zones_page = ZonesPage(ctx, self._toast)
        self._rules_page = RulesPage(ctx, self._toast)
        dashboard = DashboardPage(ctx)
        self._pages: dict[str, QWidget] = {
            "dashboard": dashboard,
            "rules": self._rules_page,
            "zones": self._zones_page,
            "monitor": MonitorPage(ctx),
            "profiles": ProfilesPage(ctx, self._toast),
            "settings": SettingsPage(ctx),
        }
        dashboard.navigate.connect(self.show_page)
        self._rules_page.navigate.connect(self.show_page)
        for page in self._pages.values():
            self._stack.addWidget(page)
        column.addWidget(self._stack, 1)
        layout.addLayout(column, 1)
        self.setCentralWidget(root)

        self._sidebar.page_requested.connect(self.show_page)
        self._sidebar.stop_requested.connect(ctx.session.stop)
        self._sidebar.profile_clicked.connect(lambda: self.show_page("profiles"))
        self._sidebar.set_profile(ctx.profiles.active().name)
        ctx.session.profile_changed.connect(self._on_profile_changed)
        ctx.session.state_changed.connect(self._on_state)

        # Segurança visível fora da janela: pílula "Vigiando", bandeja e o alvo do modo de teste.
        self._indicator = RunIndicator()
        self._indicator.stop_requested.connect(ctx.session.stop)
        self._indicator.open_requested.connect(self.bring_to_front)
        self._flashes = ClickFlashes()
        ctx.session.simulated_click.connect(self._flashes.flash)
        # Avisa (no histórico) se uma janela do Vanthor cobrir uma zona vigiada: a captura veria a janela.
        self._capture_guard = CaptureGuard(ctx.session, ctx.bus)
        self._tray = VanthorTray(
            ctx.session,
            open_window=self.bring_to_front,
            quit_app=self.quit_app,
            notifications_enabled=lambda: ctx.settings.load().notifications,
            window_is_active=self.isActiveWindow,
        )
        self._tray.show()
        self._quitting = False
        self._closed = False
        self._told_about_background = False
        # A janela nativa existe desde já (mesmo oculta): a tecla de emergência é registrada nela
        # e precisa funcionar quando o app abre direto em segundo plano (início com o Windows).
        self._ctx.session.attach_window(int(self.winId()))

        self.show_page("dashboard")
        self._on_state(ctx.session.state)

    def show_page(self, key: str) -> None:
        page = self._pages[key]
        if isinstance(page, DashboardPage):
            page.refresh()
        self._stack.setCurrentWidget(page)
        self._sidebar.select(key)

    def _on_profile_changed(self, profile: object) -> None:
        self._sidebar.set_profile(getattr(profile, "name", ""))
        self._zones_page.hide_overlay()  # as zonas desenhadas eram do perfil anterior

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_state(self, state: EngineState) -> None:
        session = self._ctx.session
        dry = session.dry_run
        label = session.emergency_label()
        self._sidebar.set_state(state, label, dry_run=dry)
        strip = "testing" if dry and state is EngineState.RUNNING else state.value
        self._run_strip.setProperty("state", strip)
        self._run_strip.style().unpolish(self._run_strip)
        self._run_strip.style().polish(self._run_strip)
        if state is EngineState.STOPPED:
            suffix = ""
        elif state is EngineState.PAUSED:
            suffix = " (pausado)"
        else:
            suffix = " (modo de teste)" if dry else " (vigiando)"
        self.setWindowTitle(f"{APP_NAME}{suffix}")

        if state is EngineState.STOPPED:
            self._indicator.hide()
            self._flashes.clear()
        elif self._ctx.settings.load().show_indicator:
            self._indicator.update_state(state, dry, session.watching_counts()[0], label)
            self._indicator.show_running()

    # -- integração com o Windows --------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._styled:
            hwnd = int(self.winId())
            try:
                style_title_bar(hwnd, Color.SURFACE, Color.LINE)
            except OSError:
                log.warning("Não foi possível estilizar a barra de título", exc_info=True)
            self._styled = True

    def nativeEvent(self, event_type: QByteArray | bytes, message: int) -> tuple[bool, int]:  # noqa: N802
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and self._ctx.session.is_emergency_hotkey(int(msg.wParam)):
                log.warning("Tecla de emergência acionada")
                self._ctx.session.emergency_stop()
                return True, 0
            if msg.message == _WM_ENDSESSION and msg.wParam:
                # Desligamento do Windows ou instalador atualizando o app: encerra de verdade
                # (o "fechar vai para a bandeja" não pode impedir isso), parando tudo com segurança.
                log.info("Windows pediu o encerramento (fim de sessão ou atualização)")
                self.quit_app()
                return True, 0
        return super().nativeEvent(event_type, message)

    def run_in_background(self) -> bool:
        return self._tray.available and self._ctx.settings.load().run_in_background

    def mark_quitting(self) -> None:
        """O app está encerrando (ex.: desligamento do Windows): fechar a janela não vai para a bandeja."""
        self._quitting = True

    def quit_app(self) -> None:
        """Encerrar de verdade (menu "Sair" da bandeja): para tudo com segurança e fecha o app."""
        self._quitting = True
        self.close()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._quitting and self.run_in_background():
            # Segundo plano: a janela some, mas o Vanthor (e as automações) seguem na bandeja.
            # A pílula "Vigiando" e a tecla de emergência continuam valendo.
            event.ignore()
            self.hide()
            self._zones_page.hide_overlay()
            if not self._told_about_background:
                self._told_about_background = True
                self._tray.inform(
                    "O Vanthor continua em segundo plano",
                    "Clique no ícone da bandeja para abrir. Para encerrar, use Sair no menu do ícone.",
                )
            return
        if self._closed:  # o Qt pode chamar de novo ao encerrar: a limpeza já foi feita
            super().closeEvent(event)
            return
        self._closed = True
        # Encerrar: nada continua clicando "escondido".
        self._zones_page.shutdown()  # confirma exclusões pendentes e remove sobreposições
        self._rules_page.shutdown()
        self._ctx.session.shutdown()
        self._indicator.close()
        self._flashes.clear()
        self._tray.hide()
        super().closeEvent(event)
        QApplication.quit()
