"""Ícones Lucide (traço consistente) recoloridos em tempo de execução."""

from __future__ import annotations

from functools import cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from vanthor.winsys.paths import resource_path


@cache
def _svg_source(name: str) -> str:
    path = resource_path("icons", f"{name}.svg")
    if not path.exists():
        raise FileNotFoundError(f"Ícone ausente: {name}")
    return path.read_text(encoding="utf-8")


def pixmap(name: str, color: str, size: int = 18, stroke: float = 1.75) -> QPixmap:
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    svg = (
        _svg_source(name)
        .replace("currentColor", color)
        .replace('stroke-width="2"', f'stroke-width="{stroke}"')
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    image = QPixmap(round(size * ratio), round(size * ratio))
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image


def icon(name: str, color: str, size: int = 18, disabled_color: str | None = None) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal)
    result.addPixmap(pixmap(name, color, size), QIcon.Mode.Active)
    if disabled_color:
        result.addPixmap(pixmap(name, disabled_color, size), QIcon.Mode.Disabled)
    return result
