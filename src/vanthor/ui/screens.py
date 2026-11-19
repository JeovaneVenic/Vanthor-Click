"""Junta as telas do Qt (lógicas) com os monitores do Windows (físicos)."""

from __future__ import annotations

import logging

from PySide6.QtGui import QGuiApplication, QScreen

from vanthor.capture.geometry import MonitorMap, ScreenRect
from vanthor.winsys.monitors import enumerate_monitors

log = logging.getLogger(__name__)


def monitor_maps() -> list[MonitorMap]:
    physical = {m.name: m for m in enumerate_monitors()}
    maps: list[MonitorMap] = []
    for index, screen in enumerate(_ordered_screens(), start=1):
        geo = screen.geometry()
        native = physical.get(screen.name())
        if native is not None:
            rect, primary = native.rect, native.primary
        else:
            # Sem par no Win32 (raro: driver remoto). Estimamos a partir do Qt e avisamos.
            log.warning("Monitor %s sem correspondência física; usando estimativa", screen.name())
            ratio = screen.devicePixelRatio()
            rect = ScreenRect(
                round(geo.x() * ratio), round(geo.y() * ratio), round(geo.width() * ratio), round(geo.height() * ratio)
            )
            primary = screen is QGuiApplication.primaryScreen()
        maps.append(
            MonitorMap(
                name=screen.name(),
                label=f"Monitor {index}",
                logical_x=geo.x(),
                logical_y=geo.y(),
                physical=rect,
                scale=rect.width / geo.width(),
                primary=primary,
            )
        )
    return maps


def map_for_screen(screen: QScreen, maps: list[MonitorMap]) -> MonitorMap:
    return next(m for m in maps if m.name == screen.name())


def screen_for_map(monitor: MonitorMap) -> QScreen | None:
    return next((s for s in QGuiApplication.screens() if s.name() == monitor.name), None)


def _ordered_screens() -> list[QScreen]:
    primary = QGuiApplication.primaryScreen()
    return sorted(
        QGuiApplication.screens(), key=lambda s: (s is not primary, s.geometry().x(), s.geometry().y())
    )
