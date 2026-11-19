"""Detector de cor: regras de tolerância, espaços de cor e critérios de detecção."""

from __future__ import annotations

import time

import numpy as np
import pytest

from vanthor.vision.color import (
    ColorCondition,
    ColorSpace,
    MatchMode,
    detect,
    hex_to_rgb,
    rgb_to_hex,
    rgb_to_hsv,
)


def solid(rgb: tuple[int, int, int], h: int = 10, w: int = 10) -> np.ndarray:
    image = np.zeros((h, w, 3), np.uint8)
    image[:, :] = rgb
    return image


GREEN = (0x22, 0xC5, 0x5E)  # #22C55E, o exemplo do projeto


# -- HEX ----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "rgb"),
    [("#22C55E", GREEN), ("22c55e", GREEN), ("  #22C55E ", GREEN), ("#2c5", (0x22, 0xCC, 0x55)), ("#000", (0, 0, 0))],
)
def test_hex_to_rgb(text: str, rgb: tuple[int, int, int]) -> None:
    assert hex_to_rgb(text) == rgb


@pytest.mark.parametrize("text", ["", "#12", "#GGGGGG", "#1234567", "rgb(1,2,3)"])
def test_hex_to_rgb_rejects_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        hex_to_rgb(text)


def test_rgb_to_hex() -> None:
    assert rgb_to_hex(GREEN) == "#22C55E"


# -- RGB e tolerância ------------------------------------------------------------------------------
def test_exact_color_is_detected_with_zero_tolerance() -> None:
    result = detect(solid(GREEN), ColorCondition(GREEN, tolerance=0))
    assert result.detected and result.matched_pixels == 100 and result.ratio == 1.0


def test_project_example_tolerance_15_is_inclusive_per_channel() -> None:
    condition = ColorCondition(GREEN, tolerance=15)
    assert detect(solid((0x22 + 15, 0xC5 - 15, 0x5E + 15)), condition).detected
    assert not detect(solid((0x22 + 16, 0xC5, 0x5E)), condition).detected
    assert not detect(solid((0x22, 0xC5, 0x5E - 16)), condition).detected


def test_tolerance_does_not_overflow_at_channel_limits() -> None:
    # uint8 sem conversão faria 0 - 1 = 255 e aceitaria cores erradas.
    condition = ColorCondition((0, 0, 0), tolerance=5)
    assert detect(solid((5, 5, 5)), condition).detected
    assert not detect(solid((255, 255, 255)), condition).detected
    assert detect(solid((250, 255, 252)), ColorCondition((255, 255, 255), tolerance=5)).detected


# -- critérios -----------------------------------------------------------------------------------
def image_with_matches(count: int, total: int = 100) -> np.ndarray:
    image = solid((0, 0, 0), 10, total // 10)
    flat = image.reshape(-1, 3)
    flat[:count] = GREEN
    return image


def test_single_pixel_is_enough_by_default() -> None:
    result = detect(image_with_matches(1), ColorCondition(GREEN))
    assert result.detected and result.matched_pixels == 1


def test_minimum_pixels_boundary() -> None:
    condition = ColorCondition(GREEN, mode=MatchMode.PIXELS, min_pixels=25)
    assert not detect(image_with_matches(24), condition).detected
    assert detect(image_with_matches(25), condition).detected


def test_minimum_percent_boundary() -> None:
    condition = ColorCondition(GREEN, mode=MatchMode.PERCENT, min_percent=30)
    assert not detect(image_with_matches(29), condition).detected
    result = detect(image_with_matches(30), condition)
    assert result.detected and result.ratio == pytest.approx(0.30)


def test_no_match_reports_zero() -> None:
    result = detect(solid((1, 2, 3)), ColorCondition(GREEN))
    assert (result.detected, result.matched_pixels, result.ratio) == (False, 0, 0.0)


def test_mask_marks_matching_pixels() -> None:
    result = detect(image_with_matches(3), ColorCondition(GREEN), with_mask=True)
    assert result.mask is not None and int(result.mask.sum()) == 3


def test_empty_image_is_never_detected() -> None:
    result = detect(np.zeros((0, 0, 3), np.uint8), ColorCondition(GREEN))
    assert not result.detected and result.total_pixels == 0


# -- HSV ------------------------------------------------------------------------------------------
def test_rgb_to_hsv_known_values() -> None:
    hsv = rgb_to_hsv(np.array([[[255, 0, 0], [0, 255, 0], [0, 0, 255], [128, 128, 128], [0, 0, 0]]], np.uint8))
    assert hsv[0, 0] == pytest.approx([0, 1, 1])
    assert hsv[0, 1] == pytest.approx([120, 1, 1])
    assert hsv[0, 2] == pytest.approx([240, 1, 1])
    assert hsv[0, 3] == pytest.approx([0, 0, 128 / 255], abs=1e-6)
    assert hsv[0, 4] == pytest.approx([0, 0, 0])


def test_hsv_accepts_same_hue_under_different_lighting() -> None:
    # Mesmo verde, 30% mais escuro: RGB com tolerância baixa recusa; HSV com 30 aceita.
    darker = tuple(int(c * 0.7) for c in GREEN)
    assert not detect(solid(darker), ColorCondition(GREEN, tolerance=15)).detected  # type: ignore[arg-type]
    assert detect(solid(darker), ColorCondition(GREEN, space=ColorSpace.HSV, tolerance=30)).detected  # type: ignore[arg-type]


def test_hsv_hue_wraps_around_red() -> None:
    # 358° e 2° estão a 4° de distância, não 356°.
    target = (255, 0, 8)  # ~358°
    pixel = (255, 8, 0)  # ~2°
    assert detect(solid(pixel), ColorCondition(target, space=ColorSpace.HSV, tolerance=5)).detected


def test_hsv_rejects_different_hue() -> None:
    assert not detect(solid((0, 0, 255)), ColorCondition(GREEN, space=ColorSpace.HSV, tolerance=30)).detected


def test_hsv_gray_target_ignores_meaningless_hue() -> None:
    # Cinzas não têm tom definido: só saturação e brilho contam.
    gray = ColorCondition((128, 128, 128), space=ColorSpace.HSV, tolerance=10)
    assert detect(solid((130, 126, 128)), gray).detected
    assert not detect(solid((200, 40, 40)), gray).detected


def test_hsv_saturated_target_rejects_grays() -> None:
    assert not detect(solid((128, 128, 128)), ColorCondition(GREEN, space=ColorSpace.HSV, tolerance=40)).detected


# -- validação e serialização --------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [
        {"tolerance": -1},
        {"tolerance": 256},
        {"space": ColorSpace.HSV, "tolerance": 101},
        {"mode": MatchMode.PIXELS, "min_pixels": 0},
        {"mode": MatchMode.PERCENT, "min_percent": 0},
        {"mode": MatchMode.PERCENT, "min_percent": 100.1},
    ],
)
def test_condition_validates_ranges(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ColorCondition(GREEN, **kwargs)  # type: ignore[arg-type]


def test_condition_rejects_invalid_color() -> None:
    with pytest.raises(ValueError):
        ColorCondition((256, 0, 0))


def test_condition_roundtrip_dict() -> None:
    condition = ColorCondition(GREEN, ColorSpace.HSV, 22, MatchMode.PERCENT, min_pixels=5, min_percent=12.5)
    assert ColorCondition.from_dict(condition.to_dict()) == condition


def test_condition_from_dict_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        ColorCondition.from_dict({"color": "#22C55E", "space": "cmyk"})
    with pytest.raises(ValueError):
        ColorCondition.from_dict({"color": 12})


# -- desempenho ----------------------------------------------------------------------------------
@pytest.mark.parametrize("space", [ColorSpace.RGB, ColorSpace.HSV])
def test_large_zone_is_analysed_quickly(space: ColorSpace) -> None:
    image = np.random.default_rng(1).integers(0, 256, (600, 800, 3), dtype=np.uint8)  # 480 mil pixels
    condition = ColorCondition(GREEN, space=space, tolerance=20)
    detect(image, condition)  # aquece
    timings = []
    for _ in range(3):  # melhor de 3: ignora picos do sistema operacional
        started = time.perf_counter()
        detect(image, condition)
        timings.append((time.perf_counter() - started) * 1000)
    # Ruído aleatório é o pior caso para o HSV (quase todo pixel vira candidato). Medido isolado:
    # ~14 ms (RGB) e ~45 ms (HSV). A margem cobre a máquina ocupada pela suíte inteira; o teste
    # existe para pegar regressões grandes (ex.: voltar a converter a imagem toda), não 10 ms.
    assert min(timings) < 120


# -- sugestão de cor ---------------------------------------------------------------------------------
def test_suggest_color_prefers_button_over_text_and_background() -> None:
    from vanthor.vision.color import suggest_color

    image = solid((30, 41, 59), 50, 130)  # fundo escuro em volta
    image[8:42, 8:122] = GREEN  # o botão
    image[22:28, 40:90] = (255, 255, 255)  # o texto, bem no centro
    assert suggest_color(image) == GREEN


def test_suggest_color_handles_tiny_zones() -> None:
    from vanthor.vision.color import suggest_color

    assert suggest_color(solid(GREEN, 1, 1)) == GREEN
    assert suggest_color(np.zeros((0, 0, 3), np.uint8)) == (255, 255, 255)
