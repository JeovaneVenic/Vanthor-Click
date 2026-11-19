from __future__ import annotations

from functools import cache

from PySide6.QtGui import QFont, QFontDatabase

from vanthor.ui.theme.tokens import Font


def _first_available(candidates: tuple[str, ...]) -> str:
    families = set(QFontDatabase.families())
    return next((name for name in candidates if name in families), candidates[-1])


@cache
def ui_family() -> str:
    return _first_available(Font.UI_CANDIDATES)


@cache
def display_family() -> str:
    return _first_available(Font.DISPLAY_CANDIDATES)


def display_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(display_family())
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    # Números com largura fixa: o cronômetro não "dança" enquanto conta.
    font.setFeature(QFont.Tag("tnum"), 1)
    return font


def ui_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(ui_family())
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font
