"""Geometria de tela em pixels FÍSICOS e a conversão a partir dos pixels lógicos do Qt.

Por que existe: com escala de 125%, o Qt informa 1536×864 para um monitor de 1920×1080. A captura
(MSS) e os cliques (SendInput) trabalham em pixels físicos. Toda conversão passa por aqui, num
único lugar testado, para que a zona desenhada seja exatamente a zona capturada.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScreenRect:
    """Retângulo em pixels físicos no desktop virtual (pode ter coordenadas negativas)."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"Retângulo sem área: {self.width}×{self.height}")

    @property
    def right(self) -> int:
        """Primeira coluna FORA do retângulo (exclusivo)."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def contains_rect(self, other: ScreenRect) -> bool:
        return self.x <= other.x and self.y <= other.y and other.right <= self.right and other.bottom <= self.bottom

    def contains_point(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def intersection(self, other: ScreenRect) -> ScreenRect | None:
        left, top = max(self.x, other.x), max(self.y, other.y)
        right, bottom = min(self.right, other.right), min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return None
        return ScreenRect(left, top, right - left, bottom - top)

    def translated(self, dx: int, dy: int) -> ScreenRect:
        return ScreenRect(self.x + dx, self.y + dy, self.width, self.height)

    @staticmethod
    def from_points(x1: int, y1: int, x2: int, y2: int) -> ScreenRect:
        """Retângulo a partir de dois cantos em qualquer ordem (arrastar para cima/esquerda também vale).

        Os pontos são inclusivos: arrastar do pixel 10 ao pixel 12 cobre 3 pixels.
        """
        left, right = min(x1, x2), max(x1, x2)
        top, bottom = min(y1, y2), max(y1, y2)
        return ScreenRect(left, top, right - left + 1, bottom - top + 1)


@dataclass(frozen=True, slots=True)
class MonitorMap:
    """Um monitor visto pelas duas lentes: lógica (Qt) e física (Windows/captura)."""

    name: str  # ex.: \\.\DISPLAY1 (estável entre reinícios)
    label: str  # ex.: "Monitor 1"
    logical_x: float
    logical_y: float
    physical: ScreenRect
    scale: float  # 1.25 para 125%
    primary: bool = False

    def to_physical(self, logical_x: float, logical_y: float) -> tuple[int, int]:
        """Converte um ponto lógico GLOBAL do Qt num pixel físico deste monitor (preso às bordas)."""
        px = self.physical.x + int((logical_x - self.logical_x) * self.scale)
        py = self.physical.y + int((logical_y - self.logical_y) * self.scale)
        return (
            min(max(px, self.physical.x), self.physical.right - 1),
            min(max(py, self.physical.y), self.physical.bottom - 1),
        )

    def to_logical(self, physical_x: int, physical_y: int) -> tuple[float, float]:
        return (
            self.logical_x + (physical_x - self.physical.x) / self.scale,
            self.logical_y + (physical_y - self.physical.y) / self.scale,
        )

    def rect_to_logical(self, rect: ScreenRect) -> tuple[float, float, float, float]:
        x, y = self.to_logical(rect.x, rect.y)
        return x, y, rect.width / self.scale, rect.height / self.scale


def monitor_for_rect(rect: ScreenRect, monitors: list[MonitorMap]) -> MonitorMap | None:
    """O monitor que contém o retângulo inteiro, ou None se ele saiu da tela (monitor desligado)."""
    return next((m for m in monitors if m.physical.contains_rect(rect)), None)


def monitor_for_point(x: int, y: int, monitors: list[MonitorMap]) -> MonitorMap | None:
    return next((m for m in monitors if m.physical.contains_point(x, y)), None)
