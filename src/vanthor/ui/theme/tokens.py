"""Tokens de design: a única fonte de cores, fontes e medidas da interface.

Direção "o olhar da pantera": superfícies grafite quietas e um único destaque, a brasa dos
olhos da logo, reservado para o que está vigiando ou para a ação principal.
"""

from __future__ import annotations

from typing import Final


class Color:
    BG: Final = "#101214"
    SURFACE: Final = "#17191C"
    RAISED: Final = "#1F2226"
    RAISED_HOVER: Final = "#262A2F"
    LINE: Final = "#2A2E33"
    LINE_STRONG: Final = "#3A3F46"

    TEXT: Final = "#E4E7EB"
    TEXT_2: Final = "#9AA1AB"
    TEXT_3: Final = "#7A818B"
    STEEL: Final = "#AEB5BF"

    BRASA: Final = "#FA6602"
    BRASA_HOVER: Final = "#FF7A1F"
    BRASA_PRESSED: Final = "#D95700"
    ON_BRASA: Final = "#1A0B00"  # texto escuro: contraste 7:1 sobre a brasa (branco daria 3:1)

    DETECT: Final = "#34D399"
    SIM: Final = "#22D3EE"
    DANGER: Final = "#F43F5E"


class Font:
    # Windows primeiro (inalterado). Depois, as equivalentes do Linux Mint: a Segoe e a Bahnschrift
    # são fontes da Microsoft e não existem nem podem ser redistribuídas no Linux.
    UI_CANDIDATES: Final = ("Segoe UI Variable Text", "Segoe UI", "Ubuntu", "Noto Sans", "DejaVu Sans")
    DISPLAY_CANDIDATES: Final = (
        "Bahnschrift", "Segoe UI Variable Display", "Segoe UI",
        "Ubuntu Condensed", "Noto Sans Display", "Noto Sans", "DejaVu Sans Condensed",
    )


class Size:
    BODY: Final = 14
    SMALL: Final = 13
    TITLE: Final = 22
    STATE: Final = 26
    STAT: Final = 30

    RADIUS_S: Final = 6
    RADIUS_M: Final = 10
    RADIUS_L: Final = 14

    CONTROL_H: Final = 40
    PRIMARY_H: Final = 48
    SIDEBAR_W: Final = 232
    PAGE_MARGIN: Final = 32
