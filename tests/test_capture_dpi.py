"""Aceite da Fase 2: a zona desenhada na tela é exatamente a área capturada, com qualquer escala.

Usa janelas, overlay e captura reais. Se a conversão lógico→físico estivesse errada (ex.: escala
de 125% ignorada), a zona cairia fora da janela de teste e as cores não bateriam.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QPainter  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402
from pytestqt.qtbot import QtBot  # noqa: E402

from vanthor.capture.capturer import RegionCapturer  # noqa: E402
from vanthor.capture.geometry import ScreenRect  # noqa: E402
from vanthor.ui.screens import map_for_screen, monitor_maps  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="captura real do Windows")

PATTERN = QColor("#12ABCD")


class PatternWindow(QWidget):
    """Janela sólida, sem borda e sempre no topo, com uma cor que não existe na tela comum."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), PATTERN)
        p.end()


def physical_rect_of(widget: QWidget) -> ScreenRect:
    screen = widget.screen()
    monitor = map_for_screen(screen, monitor_maps())
    geo = widget.geometry()
    x1, y1 = monitor.to_physical(geo.x(), geo.y())
    width, height = round(geo.width() * monitor.scale), round(geo.height() * monitor.scale)
    return ScreenRect(x1, y1, width, height)


@pytest.fixture
def pattern(qtbot: QtBot) -> PatternWindow:
    window = PatternWindow()
    qtbot.addWidget(window)
    screen_geo = QGuiApplication.primaryScreen().geometry()
    window.setGeometry(screen_geo.x() + 400, screen_geo.y() + 300, 60, 40)
    window.show()
    qtbot.waitExposed(window)
    qtbot.wait(150)  # o compositor do Windows precisa de um quadro para exibir
    return window


def is_pattern(image: np.ndarray) -> np.ndarray:
    return np.all(image == (PATTERN.red(), PATTERN.green(), PATTERN.blue()), axis=2)


def test_capture_matches_window_pixel_for_pixel(pattern: PatternWindow) -> None:
    rect = physical_rect_of(pattern)
    capturer = RegionCapturer()
    try:
        inside = capturer.grab(rect)
        assert inside.shape == (rect.height, rect.width, 3)
        assert is_pattern(inside).all(), "a captura não corresponde à área da janela"

        # Um pixel além de cada borda já não é a janela: prova que não sobra nem falta.
        border = capturer.grab(ScreenRect(rect.x - 1, rect.y - 1, rect.width + 2, rect.height + 2))
        mask = is_pattern(border)
        assert mask[1:-1, 1:-1].all()
        assert not mask[0, :].any() and not mask[-1, :].any()
        assert not mask[:, 0].any() and not mask[:, -1].any()
    finally:
        capturer.close()


def test_zone_drawn_on_overlay_captures_the_same_pixels(qtbot: QtBot, pattern: PatternWindow) -> None:
    from vanthor.ui.overlays.zone_selector import ZoneSelector

    selector = ZoneSelector("#34D399", "Teste")
    selector.start()
    overlay = next(o for o in selector._overlays if o.monitor.primary)  # noqa: SLF001
    qtbot.waitExposed(overlay)

    # Arrasta do canto superior esquerdo ao inferior direito da janela de teste (coordenadas lógicas).
    geo = pattern.geometry()
    origin = overlay.geometry().topLeft()
    start = QPoint(geo.left() - origin.x() + 2, geo.top() - origin.y() + 2)
    end = QPoint(geo.right() - origin.x() - 2, geo.bottom() - origin.y() - 2)
    qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(overlay, pos=QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
    qtbot.mouseMove(overlay, pos=end)
    qtbot.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=end)

    with qtbot.waitSignal(selector.selected, timeout=2000) as result:
        qtbot.keyClick(overlay, Qt.Key.Key_Return)
    rect, monitor_name = result.args
    assert monitor_name == overlay.monitor.name

    window_rect = physical_rect_of(pattern)
    assert window_rect.contains_rect(rect), f"zona {rect} fora da janela {window_rect}"
    # A zona tem o tamanho esperado na escala do monitor (tolerância de arredondamento: 2 px).
    scale = overlay.monitor.scale
    expected_w = (end.x() - start.x()) * scale
    assert abs(rect.width - expected_w) <= 2

    qtbot.wait(100)  # sobreposições já fechadas
    capturer = RegionCapturer()
    try:
        assert is_pattern(capturer.grab(rect)).all()
    finally:
        capturer.close()


def test_overlay_is_invisible_to_capture(qtbot: QtBot, pattern: PatternWindow) -> None:
    """A sobreposição escurecida cobre a tela, mas a captura continua vendo a tela real."""
    from vanthor.ui.overlays.zone_selector import ZoneSelector

    selector = ZoneSelector("#34D399", "Teste")
    selector.start()
    qtbot.wait(200)
    capturer = RegionCapturer()
    try:
        assert is_pattern(capturer.grab(physical_rect_of(pattern))).all()
    finally:
        capturer.close()
        selector.cancel()


def test_tiny_click_does_not_create_zone(qtbot: QtBot) -> None:
    from vanthor.ui.overlays.zone_selector import ZoneSelector

    selector = ZoneSelector("#34D399", "Teste")
    selector.start()
    overlay = selector._overlays[0]  # noqa: SLF001
    qtbot.waitExposed(overlay)
    qtbot.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=QPoint(300, 300))
    assert selector.rect is None
    with qtbot.waitSignal(selector.cancelled, timeout=1000):
        qtbot.keyClick(overlay, Qt.Key.Key_Escape)


def test_arrow_keys_nudge_by_one_physical_pixel(qtbot: QtBot) -> None:
    from vanthor.ui.overlays.zone_selector import ZoneSelector

    primary = next(m for m in monitor_maps() if m.primary)
    start = ScreenRect(primary.physical.x + 200, primary.physical.y + 200, 50, 30)
    selector = ZoneSelector("#34D399", "Teste", start)
    selector.start()
    overlay = next(o for o in selector._overlays if o.monitor.primary)  # noqa: SLF001
    qtbot.waitExposed(overlay)
    qtbot.keyClick(overlay, Qt.Key.Key_Right)
    qtbot.keyClick(overlay, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    qtbot.keyClick(overlay, Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
    assert selector.rect == ScreenRect(start.x + 1, start.y + 10, 51, 30)
    selector.cancel()
