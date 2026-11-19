"""Detecção de cor numa imagem RGB, vetorizada com NumPy.

Tolerância:
- RGB (0 a 255): cada canal pode variar até ``tolerance`` níveis. É o exemplo do projeto: #22C55E
  com tolerância 15 aceita R, G e B a até 15 níveis de distância (inclusive).
- HSV (0 a 100): tom a até ``tolerance × 1,8°`` e saturação/brilho a até ``tolerance %``. Mais
  robusto a mudanças de luz (um botão "aceso" e "apagado" muda o brilho, não o tom).
  Alvos quase cinza (saturação < 10%) ignoram o tom, que nesse caso não tem significado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
from numpy.typing import NDArray

RGB = tuple[int, int, int]

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_GRAY_SATURATION = 0.10
_HUE_DEGREES_PER_POINT = 1.8


class ColorSpace(StrEnum):
    RGB = "rgb"
    HSV = "hsv"


class MatchMode(StrEnum):
    PIXELS = "pixels"  # pelo menos N pixels da cor (N = 1: "aparecer em um pixel")
    PERCENT = "percent"  # pelo menos X% da zona


MAX_TOLERANCE: dict[ColorSpace, int] = {ColorSpace.RGB: 255, ColorSpace.HSV: 100}
DEFAULT_TOLERANCE: dict[ColorSpace, int] = {ColorSpace.RGB: 15, ColorSpace.HSV: 12}


def hex_to_rgb(text: str) -> RGB:
    match = _HEX.match(text.strip())
    if not match:
        raise ValueError(f"Cor HEX inválida: {text!r}")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def rgb_to_hex(rgb: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


@dataclass(frozen=True, slots=True)
class ColorCondition:
    color: RGB
    space: ColorSpace = ColorSpace.RGB
    tolerance: int = 15
    mode: MatchMode = MatchMode.PIXELS
    min_pixels: int = 1
    min_percent: float = 1.0

    def __post_init__(self) -> None:
        if len(self.color) != 3 or any(not isinstance(c, int) or not 0 <= c <= 255 for c in self.color):
            raise ValueError(f"Cor RGB inválida: {self.color}")
        limit = MAX_TOLERANCE[self.space]
        if not 0 <= self.tolerance <= limit:
            raise ValueError(f"Tolerância {self.tolerance} fora de 0 a {limit}")
        if self.min_pixels < 1:
            raise ValueError("A quantidade mínima de pixels é 1")
        if not 0 < self.min_percent <= 100:
            raise ValueError("A porcentagem mínima precisa estar entre 0 e 100")

    @property
    def hex(self) -> str:
        return rgb_to_hex(self.color)

    def to_dict(self) -> dict[str, Any]:
        return {
            "color": self.hex,
            "space": self.space.value,
            "tolerance": self.tolerance,
            "mode": self.mode.value,
            "min_pixels": self.min_pixels,
            "min_percent": self.min_percent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColorCondition:
        """Reconstrói a partir de JSON (perfil importado, banco). Tudo é validado: nada é confiável."""
        try:
            color = data["color"]
            if not isinstance(color, str):
                raise ValueError("cor precisa ser HEX")
            return cls(
                color=hex_to_rgb(color),
                space=ColorSpace(data.get("space", "rgb")),
                tolerance=int(data.get("tolerance", 15)),
                mode=MatchMode(data.get("mode", "pixels")),
                min_pixels=int(data.get("min_pixels", 1)),
                min_percent=float(data.get("min_percent", 1.0)),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Condição de cor inválida: {exc}") from exc


@dataclass(frozen=True, slots=True)
class DetectionResult:
    detected: bool
    matched_pixels: int
    total_pixels: int
    mask: NDArray[np.bool_] | None = None

    @property
    def ratio(self) -> float:
        return self.matched_pixels / self.total_pixels if self.total_pixels else 0.0


def detect(image: NDArray[np.uint8], condition: ColorCondition, with_mask: bool = False) -> DetectionResult:
    total = int(image.shape[0] * image.shape[1]) if image.ndim == 3 else 0
    if total == 0:
        return DetectionResult(False, 0, 0, None)
    mask = _match_rgb(image, condition) if condition.space is ColorSpace.RGB else _match_hsv(image, condition)
    matched = int(np.count_nonzero(mask))
    if condition.mode is MatchMode.PIXELS:
        detected = matched >= condition.min_pixels
    else:
        # Comparação em inteiros (pixels × 100) evita que 30% vire 29,999...% por arredondamento.
        detected = matched * 100 >= condition.min_percent * total - 1e-9
    return DetectionResult(detected, matched, total, mask if with_mask else None)


def _match_rgb(image: NDArray[np.uint8], condition: ColorCondition) -> NDArray[np.bool_]:
    # int16: em uint8, 0 - 1 daria 255 e a tolerância aceitaria cores opostas.
    diff = np.abs(image.astype(np.int16) - np.array(condition.color, np.int16))
    return np.asarray(diff.max(axis=2) <= condition.tolerance)


def _match_hsv(image: NDArray[np.uint8], condition: ColorCondition) -> NDArray[np.bool_]:
    target = rgb_to_hsv(np.array([[condition.color]], np.uint8))[0, 0]
    t = condition.tolerance / 100

    # Brilho e saturação primeiro (baratos, direto do máximo/mínimo dos canais). O tom, que é a
    # parte cara, só é calculado para os pixels que sobreviveram: em telas reais, poucos.
    maxc = image.max(axis=2).astype(np.float32)
    delta = maxc - image.min(axis=2).astype(np.float32)
    value_ok = np.abs(maxc / 255.0 - target[2]) <= t + 1e-6
    saturation = delta / np.maximum(maxc, 1.0)
    ok = value_ok & (np.abs(saturation - target[1]) <= t + 1e-6)

    if target[1] >= _GRAY_SATURATION:
        flat_ok = ok.reshape(-1)
        candidates = np.flatnonzero(flat_ok)
        if candidates.size:
            hue = rgb_to_hsv(image.reshape(-1, 3)[candidates][None, :, :])[0, :, 0]
            hue_diff = np.abs(hue - target[0])
            hue_diff = np.minimum(hue_diff, 360 - hue_diff)  # o tom é circular: 359° fica perto de 1°
            flat_ok[candidates] = hue_diff <= condition.tolerance * _HUE_DEGREES_PER_POINT + 1e-6
    return np.asarray(ok)


def suggest_color(image: NDArray[np.uint8]) -> RGB:
    """Cor mais provável que o usuário quer vigiar: a predominante no miolo da zona.

    Por que não o pixel do centro: num botão, o centro costuma cair no TEXTO. Por que o miolo
    (60% centrais): a borda da zona costuma pegar o fundo ao redor do elemento.
    """
    h, w = image.shape[:2]
    if h == 0 or w == 0:
        return (255, 255, 255)
    mh, mw = int(h * 0.2), int(w * 0.2)  # zonas minúsculas: margem 0, usa tudo
    core = image[mh : h - mh, mw : w - mw].reshape(-1, 3).astype(np.uint32)
    packed = (core[:, 0] << 16) | (core[:, 1] << 8) | core[:, 2]
    values, counts = np.unique(packed, return_counts=True)
    top = int(values[np.argmax(counts)])
    return (top >> 16) & 0xFF, (top >> 8) & 0xFF, top & 0xFF


def rgb_to_hsv(image: NDArray[np.uint8]) -> NDArray[np.float32]:
    """RGB uint8 → HSV float32 com H em graus [0, 360) e S, V em [0, 1]."""
    rgb = image.astype(np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = rgb.max(axis=-1)
    minc = rgb.min(axis=-1)
    delta = maxc - minc
    safe = np.where(delta == 0, 1.0, delta)

    hue = np.where(
        maxc == r,
        ((g - b) / safe) % 6,
        np.where(maxc == g, (b - r) / safe + 2, (r - g) / safe + 4),
    )
    hue = np.where(delta == 0, 0.0, hue * 60.0)
    saturation = np.where(maxc == 0, 0.0, delta / np.where(maxc == 0, 1.0, maxc))
    return np.stack([hue, saturation, maxc], axis=-1).astype(np.float32)
