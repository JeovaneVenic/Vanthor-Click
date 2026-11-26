"""Confiabilidade do caminho completo: tela → captura → cor → regra → clique.

Usa uma "tela" sintética (imagem NumPy com cores conhecidas) no lugar do monitor. O tempo é
simulado: cada ciclo do motor é chamado à mão, então horas de vigilância rodam em segundos.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator

import numpy as np
import pytest

from vanthor.actions.controller import InputController
from vanthor.actions.dispatcher import ActionDispatcher
from vanthor.actions.models import ActionKind, ActionSpec, ClickStep, MouseButton
from vanthor.capture import capturer as capturer_module
from vanthor.capture.capturer import CaptureError, RegionCapturer
from vanthor.capture.geometry import MonitorMap, ScreenRect
from vanthor.capture.monitor import ZoneMonitor
from vanthor.capture.zones import ZONE_COLORS, Zone
from vanthor.core.events import EventBus, EventKind
from vanthor.rules.engine import RuleEngine
from vanthor.rules.models import FireMode, Rule, RuleStatus, TriggerSettings
from vanthor.vision.color import ColorCondition, ColorSpace, MatchMode, closest_color, detect, hex_to_rgb

from .conftest import Recorder

SCREEN = ScreenRect(0, 0, 800, 600)
BACKGROUND = (30, 30, 30)
TARGET_HEX = "#22C55E"
TARGET = hex_to_rgb(TARGET_HEX)


# -- tela sintética e captura falsa ---------------------------------------------------------------
class Desktop:
    """A "tela": pinte retângulos com cores conhecidas e a captura lê daqui."""

    def __init__(self) -> None:
        self.pixels = np.zeros((SCREEN.height, SCREEN.width, 3), np.uint8)
        self.pixels[:, :] = BACKGROUND
        self.fail_next = 0  # quantas capturas seguidas devem falhar
        self.grabs = 0

    def paint(self, rect: ScreenRect, rgb: tuple[int, int, int]) -> None:
        self.pixels[rect.y : rect.bottom, rect.x : rect.right] = rgb

    def factory(self) -> FakeCapturer:
        return FakeCapturer(self)


class FakeCapturer:
    def __init__(self, desktop: Desktop) -> None:
        self._desktop = desktop

    def grab(self, rect: ScreenRect) -> np.ndarray:
        self._desktop.grabs += 1
        if self._desktop.fail_next:
            self._desktop.fail_next -= 1
            raise CaptureError("tela bloqueada (simulado)")
        return self._desktop.pixels[rect.y : rect.bottom, rect.x : rect.right].copy()

    def close(self) -> None:
        return None


class FakeBackend:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.pos = (1, 1)
        self.gate: threading.Event | None = None  # se definido, o clique só termina quando abrir

    def get_cursor(self) -> tuple[int, int]:
        return self.pos

    def set_cursor(self, x: int, y: int) -> None:
        self.pos = (x, y)

    def mouse(self, button: MouseButton, down: bool) -> None:
        if down:
            if self.gate is not None:
                self.gate.wait(5)
            self.clicks.append(self.pos)

    def key(self, vk: int, down: bool) -> None:
        return None


def no_sleep(_s: float) -> None:
    return None


def wait_for(predicate, timeout: float = 3.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def make_zone(zid: int, rect: ScreenRect, interval_ms: int = 100) -> Zone:
    return Zone(zid, 1, f"Zona {zid:02d}", ZONE_COLORS[0], rect, "", interval_ms, True)


def make_rule(rid: int, zone_id: int, click: tuple[int, int], color: tuple[int, int, int] = TARGET,
              tolerance: int = 15, **trigger: object) -> Rule:
    settings: dict[str, object] = {"confirm_frames": 2, "release_frames": 2, "cooldown_ms": 0}
    settings.update(trigger)
    return Rule(
        rid, 1, f"Regra {rid}", RuleStatus.ACTIVE, zone_id, ColorCondition(color, tolerance=tolerance),
        TriggerSettings(**settings),  # type: ignore[arg-type]
        ActionSpec(ActionKind.CLICK, (ClickStep(*click),), restore_cursor=False),
    )


class Pipeline:
    """Monta as peças reais (monitor de zonas, motor de regras, fila de cliques) sobre a tela falsa."""

    def __init__(self, bus: EventBus, zones: list[Zone], rules: list[Rule]) -> None:
        self.desktop = Desktop()
        self.backend = FakeBackend()
        self.counts: list[tuple[int, dict[str, int]]] = []
        self.monitor = ZoneMonitor(bus, screens=lambda: [SCREEN], capturer_factory=self.desktop.factory)  # type: ignore[arg-type]
        self.dispatcher = ActionDispatcher(
            bus, lambda: InputController(self.backend, no_sleep), self.record, lambda: [SCREEN]
        )
        self.rules = RuleEngine(bus, self.monitor.latest_frame, self.dispatcher, self.record)
        self.monitor.set_zones(zones)
        self.rules.set_rules(rules, {z.id: z.name for z in zones})
        self.dispatcher.arm()
        self.now = 100.0

    def record(self, rule_id: int, **counts: int) -> None:
        self.counts.append((rule_id, counts))

    def tick(self, seconds: float = 0.025) -> None:
        self.now += seconds
        self.monitor(self.now)
        self.rules(self.now)

    def run(self, seconds: float, step: float = 0.025) -> None:
        for _ in range(round(seconds / step)):
            self.tick(step)

    def close(self) -> None:
        self.dispatcher.halt()
        self.dispatcher.join()


@pytest.fixture
def two_zones(bus: EventBus) -> Iterator[Pipeline]:
    zones = [make_zone(1, ScreenRect(100, 100, 40, 30)), make_zone(2, ScreenRect(500, 400, 60, 20))]
    rules = [make_rule(1, 1, (120, 300)), make_rule(2, 2, (530, 50), color=(239, 68, 68))]
    pipeline = Pipeline(bus, zones, rules)
    yield pipeline
    pipeline.close()


# -- 1 a 3: a comparação de cor com imagens de referência ----------------------------------------
def reference_image() -> np.ndarray:
    """Botão verde com borda suavizada (antialiasing) sobre fundo escuro, como numa tela real."""
    image = np.zeros((20, 40, 3), np.uint8)
    image[:, :] = BACKGROUND
    image[5:15, 10:30] = TARGET
    image[4, 10:30] = (28, 112, 62)  # borda: mistura de verde e fundo
    return image


def test_exact_hex_color_is_found() -> None:
    result = detect(reference_image(), ColorCondition(hex_to_rgb(TARGET_HEX), tolerance=0))
    assert result.detected
    assert result.matched_pixels == 10 * 20  # exatamente o botão, nem um pixel a mais


@pytest.mark.parametrize("delta", [1, 8, 15])
def test_small_variation_within_tolerance_is_found(delta: int) -> None:
    image = reference_image()
    shifted = np.clip(image.astype(np.int16) + delta, 0, 255).astype(np.uint8)  # renderização um pouco diferente
    assert detect(shifted, ColorCondition(TARGET, tolerance=15)).detected


@pytest.mark.parametrize("delta", [16, 40])
def test_variation_outside_tolerance_is_not_found(delta: int) -> None:
    image = reference_image()
    image[5:15, 10:30] = (TARGET[0], TARGET[1] - delta, TARGET[2])
    result = detect(image, ColorCondition(TARGET, tolerance=15))
    assert not result.detected
    assert result.matched_pixels == 0


def test_antialiased_edge_and_background_are_not_false_positives() -> None:
    image = reference_image()
    image[5:15, 10:30] = BACKGROUND  # botão sumiu; só sobra a borda suavizada
    assert not detect(image, ColorCondition(TARGET, tolerance=15)).detected


def test_hsv_tolerance_still_rejects_other_hues() -> None:
    image = np.zeros((4, 4, 3), np.uint8)
    image[:, :] = (239, 68, 68)  # vermelho
    assert not detect(image, ColorCondition(TARGET, ColorSpace.HSV, tolerance=12)).detected


def test_percent_mode_counts_only_matching_area() -> None:
    image = reference_image()  # botão ocupa 200 de 800 pixels = 25%
    assert detect(image, ColorCondition(TARGET, mode=MatchMode.PERCENT, min_percent=25)).detected
    assert not detect(image, ColorCondition(TARGET, mode=MatchMode.PERCENT, min_percent=26)).detected


@pytest.mark.parametrize("text", ["#22C55E", "22c55e", " #22C55E "])
def test_hex_input_formats_give_the_same_color(text: str) -> None:
    assert hex_to_rgb(text) == (0x22, 0xC5, 0x5E)


def test_closest_color_explains_a_miss() -> None:
    image = reference_image()
    image[5:15, 10:30] = (34, 170, 94)  # o app desenhou um verde mais escuro que o configurado
    closest = closest_color(image, TARGET)
    assert closest is not None
    assert closest.color == (34, 170, 94)
    assert closest.distance == 197 - 170  # 27 > tolerância 15: por isso não detecta
    assert (closest.x, closest.y) == (10, 5)


# -- 4, 7, 8: várias zonas, clique certo e continuidade -------------------------------------------
def test_multiple_zones_detect_and_click_at_their_own_points(two_zones: Pipeline, recorder: Recorder) -> None:
    p = two_zones
    p.run(1.0)
    assert p.backend.clicks == []  # nada na tela: nada clicado

    p.desktop.paint(ScreenRect(110, 105, 10, 10), TARGET)  # cor aparece só na zona 1
    p.run(0.5)
    assert wait_for(lambda: p.backend.clicks == [(120, 300)])

    p.desktop.paint(ScreenRect(510, 405, 5, 5), (239, 68, 68))  # e agora na zona 2
    p.run(0.5)
    assert wait_for(lambda: p.backend.clicks == [(120, 300), (530, 50)])
    assert sum(1 for e in recorder.events if e.kind is EventKind.DETECTION) == 2


def test_monitoring_continues_after_each_occurrence(two_zones: Pipeline) -> None:
    p = two_zones
    spot = ScreenRect(110, 105, 10, 10)
    for expected in range(1, 6):
        p.desktop.paint(spot, TARGET)
        p.run(0.5)
        assert wait_for(lambda n=expected: len(p.backend.clicks) == n), f"aparição {expected} sem clique"
        p.desktop.paint(spot, BACKGROUND)
        p.run(0.5)
    assert p.backend.clicks == [(120, 300)] * 5


def test_color_outside_zone_is_ignored(two_zones: Pipeline) -> None:
    p = two_zones
    p.desktop.paint(ScreenRect(141, 100, 20, 20), TARGET)  # encostado na zona 1, mas fora dela
    p.run(1.0)
    time.sleep(0.05)
    assert p.backend.clicks == []


def test_brief_flash_shorter_than_confirmation_is_not_clicked(two_zones: Pipeline) -> None:
    """Confirmar em 2 quadros evita clicar num pisca de um único quadro (comportamento esperado)."""
    p = two_zones
    spot = ScreenRect(110, 105, 10, 10)
    p.desktop.paint(spot, TARGET)
    p.tick(0.1)  # um único quadro com a cor
    p.desktop.paint(spot, BACKGROUND)
    p.run(1.0)
    time.sleep(0.05)
    assert p.backend.clicks == []


# -- 9: recuperação de falhas ------------------------------------------------------------------------
def test_capture_failures_recover_and_detection_resumes(two_zones: Pipeline, recorder: Recorder) -> None:
    p = two_zones
    # ~1 s de capturas falhando (tela bloqueada, troca de resolução). As duas zonas são vizinhas e saem
    # numa captura só, a cada 100 ms: 10 falhas seguidas.
    p.desktop.fail_next = 10
    p.desktop.paint(ScreenRect(110, 105, 10, 10), TARGET)
    p.run(3.0)
    assert wait_for(lambda: p.backend.clicks == [(120, 300)])
    messages = [e.message for e in recorder.events]
    assert any("Falha na captura" in m for m in messages)
    assert any("captura normalizada" in m for m in messages)


def test_screen_listing_failure_does_not_stop_monitoring(bus: EventBus, recorder: Recorder) -> None:
    calls = {"n": 0}

    def flaky_screens() -> list[ScreenRect]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("O Windows não listou os monitores")
        return [SCREEN]

    p = Pipeline(bus, [make_zone(1, ScreenRect(100, 100, 40, 30))], [make_rule(1, 1, (120, 300))])
    p.monitor = ZoneMonitor(bus, screens=flaky_screens, capturer_factory=p.desktop.factory)  # type: ignore[arg-type]
    p.monitor.set_zones([make_zone(1, ScreenRect(100, 100, 40, 30))])
    p.rules = RuleEngine(bus, p.monitor.latest_frame, p.dispatcher, p.record)
    p.rules.set_rules([make_rule(1, 1, (120, 300))], {1: "Zona 01"})
    try:
        p.run(6.0)  # passa pela releitura que falha (a cada 5 s)
        p.desktop.paint(ScreenRect(110, 105, 10, 10), TARGET)
        p.run(0.5)  # antes: a exceção subia e o motor parava; agora segue com a última lista
        assert wait_for(lambda: p.backend.clicks == [(120, 300)])
        assert any("listar os monitores" in e.message for e in recorder.events)
    finally:
        p.close()


def test_counter_write_failure_does_not_stop_clicks(bus: EventBus, recorder: Recorder) -> None:
    p = Pipeline(bus, [make_zone(1, ScreenRect(100, 100, 40, 30))], [make_rule(1, 1, (120, 300))])

    def broken_db(_rule_id: int, **_counts: int) -> None:
        raise RuntimeError("database is locked")

    p.rules = RuleEngine(bus, p.monitor.latest_frame, p.dispatcher, broken_db)
    p.rules.set_rules([make_rule(1, 1, (120, 300))], {1: "Zona 01"})
    try:
        p.desktop.paint(ScreenRect(110, 105, 10, 10), TARGET)
        p.run(0.5)  # antes: a exceção derrubava o motor ("Automações paradas por erro")
        assert wait_for(lambda: p.backend.clicks == [(120, 300)])
        assert sum("gravar os contadores" in e.message for e in recorder.events) == 1  # avisa uma vez
    finally:
        p.close()


def test_region_capturer_opens_a_new_session_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Um contexto gráfico inválido (tela bloqueada, resolução trocada) não pode ser reusado para sempre."""
    sessions: list[object] = []

    class Shot:
        width, height = 2, 2
        bgra = bytes(2 * 2 * 4)

    class FakeMSS:
        def __init__(self) -> None:
            self.broken = not sessions  # só a primeira sessão está quebrada
            sessions.append(self)

        def grab(self, _region: dict[str, int]) -> Shot:
            if self.broken:
                raise capturer_module.mss.ScreenShotError("gdi32.GetDIBits() failed")
            return Shot()

        def close(self) -> None:
            return None

    monkeypatch.setattr(capturer_module.mss, "MSS", FakeMSS)
    capturer = RegionCapturer()
    with pytest.raises(CaptureError):
        capturer.grab(ScreenRect(0, 0, 2, 2))
    image = capturer.grab(ScreenRect(0, 0, 2, 2))  # antes: reusava a sessão quebrada e falhava de novo
    assert image.shape == (2, 2, 3)
    assert len(sessions) == 2


def test_rearming_while_previous_action_thread_is_finishing_still_clicks(bus: EventBus) -> None:
    """Parar e iniciar de novo enquanto um clique antigo termina não pode silenciar a nova execução."""
    backend = FakeBackend()
    backend.gate = threading.Event()
    dispatcher = ActionDispatcher(bus, lambda: InputController(backend, no_sleep), lambda *_a, **_k: None,
                                  lambda: [SCREEN])
    rule = make_rule(1, 1, (10, 10))
    dispatcher.arm()
    dispatcher.dispatch(rule)
    assert wait_for(lambda: backend.pos == (10, 10))  # thread de ações presa no meio do clique
    dispatcher.halt()  # parada (sem esperar a thread)
    dispatcher.arm()  # nova execução logo em seguida
    backend.gate.set()
    dispatcher.dispatch(make_rule(2, 1, (20, 20)))
    try:
        assert wait_for(lambda: (20, 20) in backend.clicks), "a nova execução descartou o clique"
    finally:
        dispatcher.halt()
        dispatcher.join()


# -- 10: vigilância prolongada ------------------------------------------------------------------------
def test_long_monitoring_session_never_misses_or_duplicates(bus: EventBus) -> None:
    """Duas horas simuladas: a cor aparece a cada 2 minutos por 3 s. Um clique por aparição, sempre."""
    p = Pipeline(bus, [make_zone(1, ScreenRect(100, 100, 40, 30), interval_ms=250)], [make_rule(1, 1, (120, 300))])
    spot = ScreenRect(110, 105, 10, 10)
    try:
        appearances = 60
        for index in range(appearances):
            p.run(117.0, step=0.25)
            p.desktop.paint(spot, TARGET)
            p.run(3.0, step=0.25)
            p.desktop.paint(spot, BACKGROUND)
            assert wait_for(lambda n=index + 1: len(p.backend.clicks) == n), f"aparição {index + 1} sem clique"
        p.run(5.0, step=0.25)
        time.sleep(0.05)
        assert len(p.backend.clicks) == appearances
        assert p.desktop.grabs >= 2 * 3600 * 4 * 0.99  # 4 capturas por segundo, sem buracos
    finally:
        p.close()


# -- diagnóstico ----------------------------------------------------------------------------------------
def test_diagnostic_log_shows_config_and_closest_color(two_zones: Pipeline, caplog: pytest.LogCaptureFixture) -> None:
    p = two_zones
    caplog.set_level(logging.INFO, logger="vanthor")
    p.desktop.paint(ScreenRect(110, 105, 10, 10), (34, 170, 94))  # verde parecido, mas fora da tolerância
    p.run(61.0, step=0.1)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "pixel mais parecido com #22C55E: #22AA5E (diferença 27, tolerância 15)" in text
    assert "máximo de 0 de 1200 pixels com a cor" in text
    assert p.backend.clicks == []


def test_rule_config_is_logged_when_watching_starts(bus: EventBus, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="vanthor")
    p = Pipeline(bus, [make_zone(1, ScreenRect(100, 100, 40, 30))], [make_rule(1, 1, (120, 300))])
    p.close()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Vigiando Zona 01 (id 1): x=100 y=100 40x30 px a cada 100 ms" in text
    assert "cor #22C55E, RGB tolerância 15, pelo menos 1 pixel(s)" in text


def test_click_coordinates_are_logged(two_zones: Pipeline, caplog: pytest.LogCaptureFixture) -> None:
    p = two_zones
    caplog.set_level(logging.INFO, logger="vanthor")
    p.desktop.paint(ScreenRect(110, 105, 10, 10), TARGET)
    p.run(0.5)
    assert wait_for(lambda: p.backend.clicks == [(120, 300)])
    assert wait_for(lambda: any("clique left em 120,300" in r.getMessage() for r in caplog.records))


# -- proteção de captura (janelas do Vanthor sobre zonas) ----------------------------------------------
def test_window_rect_converts_to_physical_with_scaling() -> None:
    from PySide6.QtCore import QRect

    from vanthor.ui.capture_guard import physical_rects

    monitor = MonitorMap("M1", "Monitor 1", 0, 0, ScreenRect(0, 0, 1920, 1080), 1.25, True)
    second = MonitorMap("M2", "Monitor 2", 1536, 0, ScreenRect(1920, 0, 1920, 1080), 1.25)
    pieces = physical_rects(QRect(100, 100, 400, 200), [monitor, second])
    assert pieces == [ScreenRect(125, 125, 500, 250)]
    across = physical_rects(QRect(1436, 0, 200, 100), [monitor, second])  # metade em cada monitor
    assert across == [ScreenRect(1795, 0, 125, 125), ScreenRect(1920, 0, 125, 125)]


def test_once_mode_fires_a_single_time_per_run(bus: EventBus) -> None:
    p = Pipeline(bus, [make_zone(1, ScreenRect(100, 100, 40, 30))],
                 [make_rule(1, 1, (120, 300), mode=FireMode.ONCE)])
    spot = ScreenRect(110, 105, 10, 10)
    try:
        for _ in range(3):
            p.desktop.paint(spot, TARGET)
            p.run(0.5)
            p.desktop.paint(spot, BACKGROUND)
            p.run(0.5)
        time.sleep(0.05)
        assert p.backend.clicks == [(120, 300)]
    finally:
        p.close()
