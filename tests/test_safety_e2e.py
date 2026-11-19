"""Aceite da Fase 6 com o aplicativo real: modo de teste, limite por minuto, indicador e monitoramento."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtGui import QColor, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from pytestqt.qtbot import QtBot  # noqa: E402

from vanthor.actions.controller import InputController  # noqa: E402
from vanthor.actions.models import ActionKind, ActionSpec, ClickStep  # noqa: E402
from vanthor.core.engine import EngineState  # noqa: E402
from vanthor.core.events import EventKind  # noqa: E402
from vanthor.persistence.database import Database  # noqa: E402
from vanthor.persistence.repositories import AppSettings  # noqa: E402
from vanthor.rules.models import FireMode, TriggerSettings  # noqa: E402
from vanthor.vision.color import ColorCondition  # noqa: E402
from vanthor.winsys.input import Win32Input  # noqa: E402

from .test_capture_dpi import physical_rect_of  # noqa: E402
from .test_input_real import Target, target  # noqa: E402, F401
from .test_rules_e2e import Lamp, rgb  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="tela e entrada reais do Windows")
GREEN = QColor("#22C55E")


@pytest.fixture
def app_window(qtbot: QtBot, tmp_path: Path) -> Iterator[object]:
    from vanthor.app import build_context
    from vanthor.ui.main_window import MainWindow
    from vanthor.ui.theme.stylesheet import apply_theme

    apply_theme(QApplication.instance())  # type: ignore[arg-type]
    db = Database(tmp_path / "safety.db")
    ctx = build_context(db)
    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.showMinimized()
    qtbot.wait(100)
    original = InputController(Win32Input()).cursor()
    yield window
    ctx.session.shutdown()
    window._indicator.close()  # noqa: SLF001
    window._tray.hide()  # noqa: SLF001
    db.close()
    InputController(Win32Input()).move(*original)


def green_lamp(qtbot: QtBot, y: int, color: QColor = GREEN) -> Lamp:
    lamp = Lamp(color)
    qtbot.addWidget(lamp)
    geo = QGuiApplication.primaryScreen().geometry()
    lamp.setGeometry(geo.x() + 300, geo.y() + y, 40, 30)
    lamp.show()
    qtbot.waitExposed(lamp)
    qtbot.wait(150)
    return lamp


def test_dry_run_shows_point_without_clicking(
    qtbot: QtBot, app_window: object, target: Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Garantia do modo de teste: o Vanthor NUNCA chama o Windows para mover o mouse ou clicar.
    # (Conferir a posição do cursor falharia se a pessoa mexesse no mouse durante o teste.)
    input_calls: list[str] = []
    monkeypatch.setattr(Win32Input, "set_cursor", lambda self, x, y: input_calls.append("mover"))
    monkeypatch.setattr(Win32Input, "mouse", lambda self, b, down: input_calls.append("clicar"))
    monkeypatch.setattr(Win32Input, "key", lambda self, vk, down: input_calls.append("tecla"))
    ctx = app_window._ctx  # type: ignore[attr-defined]
    profile = ctx.profiles.active().id
    lamp = green_lamp(qtbot, 320, QColor("#30343A"))
    zone = ctx.zones.create(profile, physical_rect_of(lamp), "z", interval_ms=50)
    trect = physical_rect_of(target)
    point = (trect.x + 30, trect.y + 30)
    ctx.rules.create(profile, "Clicar no alvo", zone.id, ColorCondition(rgb(GREEN)), TriggerSettings(confirm_frames=1),
                     ActionSpec(ActionKind.CLICK, (ClickStep(*point),)))
    flashes: list[tuple[int, int, str]] = []
    ctx.session.simulated_click.connect(lambda x, y, label: flashes.append((x, y, label)))
    simulated: list[str] = []
    ctx.session.event_received.connect(
        lambda e: simulated.append(e.message) if e.kind is EventKind.SIMULATED_ACTION else None
    )
    assert ctx.session.start(dry_run=True)
    assert ctx.session.dry_run
    lamp.set_color(GREEN)
    qtbot.waitUntil(lambda: bool(simulated), timeout=3000)
    qtbot.wait(300)

    assert target.clicks == []  # nada foi clicado de verdade
    assert input_calls == []  # nenhuma chamada de mouse ou teclado ao Windows
    assert flashes and flashes[0][:2] == point  # o alvo ciano apareceu no ponto certo
    assert "teria executado: clique esquerdo" in simulated[0]
    totals = ctx.rules.totals(profile)
    assert (totals.detections, totals.clicks) == (0, 0)  # estatísticas reais intactas
    ctx.session.stop()
    assert not ctx.session.dry_run


def test_click_limit_per_minute_in_real_run(qtbot: QtBot, app_window: object, target: Target) -> None:
    ctx = app_window._ctx  # type: ignore[attr-defined]
    ctx.settings.save(AppSettings(max_clicks_per_minute=2))
    profile = ctx.profiles.active().id
    lamp = green_lamp(qtbot, 400)
    zone = ctx.zones.create(profile, physical_rect_of(lamp), "z", interval_ms=50)
    trect = physical_rect_of(target)
    fast = TriggerSettings(FireMode.CONTINUOUS, confirm_frames=1, cooldown_ms=100)
    ctx.rules.create(profile, "Rajada", zone.id, ColorCondition(rgb(GREEN)), fast,
                     ActionSpec(ActionKind.CLICK, (ClickStep(trect.x + 20, trect.y + 20),)))
    warnings: list[str] = []
    ctx.session.event_received.connect(lambda e: warnings.append(e.message) if e.kind is EventKind.ERROR else None)

    assert ctx.session.start()
    qtbot.waitUntil(lambda: bool(warnings), timeout=4000)
    qtbot.wait(600)  # várias tentativas a cada 100 ms, todas barradas
    ctx.session.stop()
    # Dois cliques em 100 ms no mesmo ponto: o Qt entrega o segundo como "clique duplo".
    assert len(target.clicks) + target.doubles == 2
    assert "Limite de 2 cliques por minuto" in warnings[0]
    assert ctx.rules.totals(profile).clicks == 2


def test_indicator_follows_state_and_setting(qtbot: QtBot, app_window: object) -> None:
    ctx = app_window._ctx  # type: ignore[attr-defined]
    indicator = app_window._indicator  # type: ignore[attr-defined]  # noqa: SLF001
    assert ctx.session.start()
    qtbot.waitUntil(indicator.isVisible, timeout=2000)
    ctx.session.toggle_pause()
    qtbot.waitUntil(lambda: "Pausado" in indicator._text.text(), timeout=2000)  # noqa: SLF001
    ctx.session.stop()
    qtbot.waitUntil(lambda: not indicator.isVisible(), timeout=2000)

    ctx.settings.save(AppSettings(show_indicator=False))
    assert ctx.session.start()
    qtbot.waitUntil(lambda: ctx.session.state is EngineState.RUNNING, timeout=2000)
    qtbot.wait(200)
    assert not indicator.isVisible()
    ctx.session.stop()


def test_monitor_page_shows_live_rule(qtbot: QtBot, app_window: object) -> None:
    ctx = app_window._ctx  # type: ignore[attr-defined]
    profile = ctx.profiles.active().id
    lamp = green_lamp(qtbot, 480)
    zone = ctx.zones.create(profile, physical_rect_of(lamp), "Lâmpada", interval_ms=50)
    ctx.rules.create(profile, "Ver verde", zone.id, ColorCondition(rgb(GREEN)), TriggerSettings(confirm_frames=1),
                     ActionSpec())
    app_window.showNormal()  # type: ignore[attr-defined]
    app_window.show_page("monitor")  # type: ignore[attr-defined]
    page = app_window._pages["monitor"]  # type: ignore[attr-defined]  # noqa: SLF001
    lamp.raise_()
    assert ctx.session.start()
    qtbot.waitUntil(lambda: page._rules.item(0, 4) is not None and page._rules.item(0, 4).text() == "1",  # noqa: SLF001
                    timeout=3000)
    assert page._rules.item(0, 0).text() == "Ver verde"  # noqa: SLF001
    assert page._rules.item(0, 2).text() == "Cor na tela"  # noqa: SLF001
    assert page._zones.item(0, 3).text() == "Capturando"  # noqa: SLF001
    assert "Vigiando" in page._summary.text()  # noqa: SLF001
    ctx.session.stop()
