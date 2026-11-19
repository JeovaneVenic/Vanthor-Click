from __future__ import annotations

import pytest

from vanthor.capture.geometry import MonitorMap, ScreenRect, monitor_for_point, monitor_for_rect

# Cenário realista: notebook 1920×1080 a 125% como principal e, à ESQUERDA, um monitor
# 2560×1440 a 100% (coordenadas físicas negativas).
LAPTOP = MonitorMap("\\\\.\\DISPLAY1", "Monitor 1", 0, 0, ScreenRect(0, 0, 1920, 1080), 1.25, primary=True)
EXTERNAL = MonitorMap("\\\\.\\DISPLAY2", "Monitor 2", -2560, 0, ScreenRect(-2560, 0, 2560, 1440), 1.0)
MONITORS = [LAPTOP, EXTERNAL]


def test_rect_rejects_empty_area() -> None:
    with pytest.raises(ValueError):
        ScreenRect(0, 0, 0, 10)
    with pytest.raises(ValueError):
        ScreenRect(0, 0, 10, -1)


@pytest.mark.parametrize(
    ("p1", "p2"),
    [((10, 20), (49, 59)), ((49, 59), (10, 20)), ((10, 59), (49, 20)), ((49, 20), (10, 59))],
)
def test_from_points_accepts_any_drag_direction(p1: tuple[int, int], p2: tuple[int, int]) -> None:
    assert ScreenRect.from_points(*p1, *p2) == ScreenRect(10, 20, 40, 40)


def test_from_points_single_pixel_is_1x1() -> None:
    assert ScreenRect.from_points(5, 5, 5, 5) == ScreenRect(5, 5, 1, 1)


def test_right_and_bottom_are_exclusive() -> None:
    rect = ScreenRect(10, 10, 5, 5)
    assert rect.contains_point(14, 14)
    assert not rect.contains_point(15, 10)
    assert not rect.contains_point(10, 15)


def test_contains_rect_and_intersection() -> None:
    outer = ScreenRect(0, 0, 100, 100)
    assert outer.contains_rect(ScreenRect(0, 0, 100, 100))
    assert not outer.contains_rect(ScreenRect(50, 50, 51, 10))
    assert outer.intersection(ScreenRect(90, 90, 20, 20)) == ScreenRect(90, 90, 10, 10)
    assert outer.intersection(ScreenRect(100, 0, 5, 5)) is None


def test_logical_to_physical_at_125_percent() -> None:
    # 1536×864 lógico ↔ 1920×1080 físico
    assert LAPTOP.to_physical(0, 0) == (0, 0)
    assert LAPTOP.to_physical(100, 200) == (125, 250)
    assert LAPTOP.to_physical(1535.99, 863.99) == (1919, 1079)


def test_logical_to_physical_clamps_to_monitor_edges() -> None:
    assert LAPTOP.to_physical(1536, 864) == (1919, 1079)
    assert LAPTOP.to_physical(-5, -5) == (0, 0)


def test_monitor_with_negative_coordinates() -> None:
    assert EXTERNAL.to_physical(-2560, 0) == (-2560, 0)
    assert EXTERNAL.to_physical(-1, 1439) == (-1, 1439)


def test_physical_to_logical_roundtrip() -> None:
    for px, py in [(0, 0), (125, 250), (1919, 1079), (777, 333)]:
        lx, ly = LAPTOP.to_logical(px, py)
        assert LAPTOP.to_physical(lx, ly) == (px, py)


def test_rect_to_logical_scales_size() -> None:
    assert LAPTOP.rect_to_logical(ScreenRect(125, 250, 250, 125)) == (100, 200, 200, 100)


def test_monitor_lookup() -> None:
    assert monitor_for_rect(ScreenRect(100, 100, 50, 50), MONITORS) is LAPTOP
    assert monitor_for_rect(ScreenRect(-200, 100, 50, 50), MONITORS) is EXTERNAL
    # Atravessa a divisa entre monitores: não pertence a nenhum (zona sempre num monitor só).
    assert monitor_for_rect(ScreenRect(-10, 100, 50, 50), MONITORS) is None
    # Monitor desligado: zona fica "fora da tela".
    assert monitor_for_rect(ScreenRect(-200, 100, 50, 50), [LAPTOP]) is None
    assert monitor_for_point(-1, 0, MONITORS) is EXTERNAL
    assert monitor_for_point(5000, 0, MONITORS) is None
