"""Zona: uma área da tela que o Vanthor observa."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from vanthor.capture.geometry import ScreenRect

# Cores de identificação: distintas entre si e da brasa (que significa "vigiando").
ZONE_COLORS: Final = (
    "#34D399",  # verde
    "#60A5FA",  # azul
    "#A78BFA",  # roxo
    "#F472B6",  # rosa
    "#FBBF24",  # amarelo
    "#22D3EE",  # ciano
    "#A3E635",  # lima
    "#F87171",  # coral
)

# Opções de frequência oferecidas na interface (ms). Mais rápido = mais CPU.
INTERVAL_CHOICES_MS: Final = (50, 100, 250, 500, 1000)
DEFAULT_INTERVAL_MS: Final = 100
MIN_INTERVAL_MS: Final = 20
MAX_INTERVAL_MS: Final = 60_000

MIN_ZONE_SIDE: Final = 2  # px físicos; menor que isso é quase sempre um clique sem arrastar


@dataclass(frozen=True, slots=True)
class Zone:
    id: int
    profile_id: int
    name: str
    color: str
    rect: ScreenRect
    monitor: str
    interval_ms: int
    enabled: bool
    position: int = 0

    def with_changes(self, **changes: object) -> Zone:
        return replace(self, **changes)  # type: ignore[arg-type]


def next_zone_name(existing: list[str]) -> str:
    """Primeiro "Zona NN" livre, para nomes curtos e previsíveis."""
    used = set(existing)
    number = 1
    while f"Zona {number:02d}" in used:
        number += 1
    return f"Zona {number:02d}"


def next_zone_color(existing: list[str]) -> str:
    """Cor menos usada; em empate, segue a ordem da paleta."""
    counts = {color: existing.count(color) for color in ZONE_COLORS}
    return min(ZONE_COLORS, key=lambda c: (counts[c], ZONE_COLORS.index(c)))


def interval_label(interval_ms: int) -> str:
    per_second = 1000 / interval_ms
    if per_second >= 1:
        times = f"{per_second:g}".replace(".", ",")
        return f"{times}× por segundo"
    seconds = f"{interval_ms / 1000:g}".replace(".", ",")
    return f"A cada {seconds} s"
