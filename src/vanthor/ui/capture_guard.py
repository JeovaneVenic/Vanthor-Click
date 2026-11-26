"""Avisa quando uma janela do Vanthor cobre uma zona vigiada.

Por que existe: a captura lê o que está NA TELA. Se a janela do Vanthor (ou um diálogo dele, ou a
pílula "Vigiando" no Linux) estiver por cima de uma zona, o motor vê a janela, não o aplicativo
vigiado: a cor esperada "nunca aparece", ou a cor da própria interface vira um falso positivo.
No Windows as sobreposições já ficam fora da captura; a janela principal e os diálogos, não.
No Linux (X11) nenhuma janela pode ser excluída da captura.

Não muda nada na tela: só registra um aviso no histórico (uma vez por episódio) e no log técnico.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QRect, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QMenu, QWidget

from vanthor.capture.geometry import MonitorMap, ScreenRect
from vanthor.core.engine import EngineState
from vanthor.core.events import Event, EventBus, EventKind, EventLevel
from vanthor.ui.overlays.run_indicator import RunIndicator
from vanthor.ui.screens import monitor_maps
from vanthor.ui.session import AutomationSession
from vanthor.winsys.desktop import is_excluded_from_capture

log = logging.getLogger(__name__)

_CHECK_MS = 1000


def physical_rects(logical: QRect, monitors: list[MonitorMap]) -> list[ScreenRect]:
    """Converte um retângulo lógico do Qt nos pedaços físicos que ele ocupa em cada monitor."""
    pieces: list[ScreenRect] = []
    for monitor in monitors:
        lx, ly, lw, lh = monitor.rect_to_logical(monitor.physical)
        left, top = max(float(logical.x()), lx), max(float(logical.y()), ly)
        right = min(float(logical.x() + logical.width()), lx + lw)
        bottom = min(float(logical.y() + logical.height()), ly + lh)
        if right <= left or bottom <= top:
            continue
        # Sem o "prender às bordas" do to_physical: aqui a borda direita é exclusiva.
        x1 = monitor.physical.x + round((left - monitor.logical_x) * monitor.scale)
        y1 = monitor.physical.y + round((top - monitor.logical_y) * monitor.scale)
        x2 = monitor.physical.x + round((right - monitor.logical_x) * monitor.scale)
        y2 = monitor.physical.y + round((bottom - monitor.logical_y) * monitor.scale)
        if x2 > x1 and y2 > y1:
            piece = ScreenRect(x1, y1, x2 - x1, y2 - y1).intersection(monitor.physical)
            if piece is not None:
                pieces.append(piece)
    return pieces


def covering_windows() -> list[tuple[str, QWidget]]:
    """Janelas do app que aparecem na captura agora (visíveis, opacas e não excluídas)."""
    found: list[tuple[str, QWidget]] = []
    for widget in QApplication.topLevelWidgets():
        # Só as janelas de verdade do Vanthor: principal, diálogos, menus e a pílula "Vigiando".
        if not isinstance(widget, (QMainWindow, QDialog, QMenu, RunIndicator)):
            continue
        if not widget.isVisible() or widget.isMinimized() or widget.windowOpacity() <= 0:
            continue
        # Sobreposições "fantasma" (borda das zonas, alvo do modo de teste) não recebem clique e são
        # desenhadas FORA das zonas: não contam.
        if widget.windowFlags() & Qt.WindowType.WindowTransparentForInput:
            continue
        if is_excluded_from_capture(int(widget.winId())):
            continue
        found.append((widget.windowTitle() or "Vanthor", widget))
    return found


class CaptureGuard(QObject):
    def __init__(self, session: AutomationSession, bus: EventBus) -> None:
        super().__init__()
        self._session = session
        self._bus = bus
        self._covered: set[int] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(_CHECK_MS)
        self._timer.timeout.connect(self.check)
        session.state_changed.connect(self._on_state)

    def _on_state(self, state: EngineState) -> None:
        if state is EngineState.STOPPED:
            self._timer.stop()
            self._covered.clear()
        elif not self._timer.isActive():
            self._timer.start()
            self.check()

    def check(self) -> None:
        if self._session.state is EngineState.STOPPED:
            return
        try:
            zones = self._session.current_plan().zones
            windows = covering_windows()
            monitors = monitor_maps() if windows else []
        except Exception:
            log.exception("Falha ao conferir se janelas cobrem zonas")
            return
        window_rects = [(title, physical_rects(w.frameGeometry(), monitors)) for title, w in windows]
        now_covered: set[int] = set()
        for zone in zones:
            for _title, rects in window_rects:
                if any(r.intersection(zone.rect) is not None for r in rects):
                    now_covered.add(zone.id)
                    break
        for zone in zones:
            if zone.id in now_covered and zone.id not in self._covered:
                log.warning("Uma janela do Vanthor está sobre %s (id %s, %s): a captura vê a janela, não a tela",
                            zone.name, zone.id, zone.rect)
                self._bus.publish(
                    Event(
                        EventKind.ERROR,
                        f"{zone.name}: uma janela do Vanthor está por cima da zona. Enquanto estiver lá, "
                        "a cor embaixo dela não é vista. Mova ou minimize a janela.",
                        EventLevel.WARNING,
                    )
                )
            elif zone.id in self._covered and zone.id not in now_covered:
                log.info("%s (id %s) voltou a ficar livre de janelas do Vanthor", zone.name, zone.id)
        self._covered = now_covered
