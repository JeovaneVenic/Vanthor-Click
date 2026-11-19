"""Ícone na bandeja do Windows: estado de relance, atalhos e avisos importantes."""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from vanthor import APP_NAME
from vanthor.core.engine import EngineState
from vanthor.core.events import Event, EventKind, EventLevel
from vanthor.ui.copy import STATE_LABEL
from vanthor.ui.session import AutomationSession
from vanthor.winsys.paths import resource_path

_NOTICE_INTERVAL_S = 10.0  # avisos de erro repetidos não viram uma chuva de notificações


class VanthorTray(QObject):
    def __init__(
        self,
        session: AutomationSession,
        open_window: Callable[[], None],
        quit_app: Callable[[], None],
        notifications_enabled: Callable[[], bool],
        window_is_active: Callable[[], bool],
    ) -> None:
        super().__init__()
        self._session = session
        self._notifications_enabled = notifications_enabled
        self._window_is_active = window_is_active
        self._last_notice = -_NOTICE_INTERVAL_S

        self._icon = QSystemTrayIcon(QIcon(str(resource_path("brand", "app.ico"))))
        menu = QMenu()
        open_action = QAction("Abrir o Vanthor", menu)
        open_action.triggered.connect(open_window)
        menu.addAction(open_action)
        self._toggle = QAction("", menu)
        self._toggle.triggered.connect(self._toggle_run)
        menu.addAction(self._toggle)
        menu.addSeparator()
        quit_action = QAction("Sair", menu)
        quit_action.triggered.connect(quit_app)
        menu.addAction(quit_action)
        self._menu = menu
        self._icon.setContextMenu(menu)
        self._icon.activated.connect(
            lambda reason: open_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        session.state_changed.connect(self._on_state)
        session.event_received.connect(self._on_event)
        self._on_state(session.state)

    @property
    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        if self.available:
            self._icon.show()

    def hide(self) -> None:
        self._icon.hide()

    def _toggle_run(self) -> None:
        if self._session.state is EngineState.STOPPED:
            self._session.start()
        else:
            self._session.stop()

    def _on_state(self, state: EngineState) -> None:
        label = "Modo de teste" if self._session.dry_run else STATE_LABEL[state]
        self._icon.setToolTip(f"{APP_NAME}: {label}")
        self._toggle.setText("Iniciar automações" if state is EngineState.STOPPED else "Parar automações")

    def _on_event(self, event: Event) -> None:
        if not self._notifications_enabled() or self._window_is_active():
            return
        if event.kind is EventKind.EMERGENCY_STOP:
            self._notify("Parada de emergência", "Todas as automações foram paradas.", critical=True)
        elif event.kind is EventKind.ERROR or (event.kind is EventKind.ENGINE_STOPPED and event.level is not EventLevel.INFO):
            now = time.monotonic()
            if now - self._last_notice >= _NOTICE_INTERVAL_S:
                self._last_notice = now
                self._notify("Atenção", event.message, critical=False)

    def inform(self, title: str, message: str) -> None:
        """Aviso neutro (não depende da opção de notificações de erro)."""
        if self.available:
            self._icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)

    def _notify(self, title: str, message: str, critical: bool) -> None:
        if not self.available:
            return
        icon = QSystemTrayIcon.MessageIcon.Critical if critical else QSystemTrayIcon.MessageIcon.Warning
        self._icon.showMessage(title, message, icon, 6000)
