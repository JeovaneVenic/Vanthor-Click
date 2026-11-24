"""Fase 7 com a interface real: troca de perfil, bloqueio durante a automação, exportar/importar."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
from pytestqt.qtbot import QtBot  # noqa: E402

from vanthor.actions.models import ActionSpec  # noqa: E402
from vanthor.capture.geometry import ScreenRect  # noqa: E402
from vanthor.persistence.database import Database  # noqa: E402
from vanthor.rules.models import RuleStatus, TriggerSettings  # noqa: E402
from vanthor.vision.color import ColorCondition  # noqa: E402

from .native import REAL_DESKTOP  # noqa: E402

pytestmark = pytest.mark.skipif(not REAL_DESKTOP, reason="tela real (Windows ou Linux/X11)")


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> Iterator[object]:
    from vanthor.app import build_context
    from vanthor.ui.main_window import MainWindow
    from vanthor.ui.theme.stylesheet import apply_theme

    apply_theme(QApplication.instance())  # type: ignore[arg-type]
    db = Database(tmp_path / "profiles.db")
    ctx = build_context(db)
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    yield win
    ctx.session.shutdown()
    win._indicator.close()  # noqa: SLF001
    win._tray.hide()  # noqa: SLF001
    db.close()


def test_switching_profile_changes_what_is_watched(qtbot: QtBot, window: object) -> None:
    ctx = window._ctx  # type: ignore[attr-defined]
    default = ctx.profiles.active()
    zone = ctx.zones.create(default.id, ScreenRect(0, 0, 10, 10), "m")
    ctx.rules.create(default.id, "Do padrão", zone.id, ColorCondition((1, 2, 3)), TriggerSettings(), ActionSpec())

    window.show_page("profiles")  # type: ignore[attr-defined]
    page = window._pages["profiles"]  # type: ignore[attr-defined]  # noqa: SLF001
    page.create()
    new = next(c.summary.profile for c in page._cards if c.summary.profile.name == "Novo perfil")  # noqa: SLF001
    page.use(new.id)

    assert ctx.profiles.active().id == new.id
    assert window._sidebar._profile.text().strip() == "Novo perfil"  # noqa: SLF001
    assert ctx.session.current_plan().rules == []  # as regras do outro perfil não são vigiadas
    window.show_page("rules")  # type: ignore[attr-defined]
    rules_page = window._pages["rules"]  # type: ignore[attr-defined]  # noqa: SLF001
    assert rules_page._cards == {}  # noqa: SLF001

    page.use(default.id)
    assert [r.name for r in ctx.session.current_plan().rules] == ["Do padrão"]


def test_switch_is_blocked_while_running(qtbot: QtBot, window: object) -> None:
    ctx = window._ctx  # type: ignore[attr-defined]
    other = ctx.profiles.create("Outro")
    page = window._pages["profiles"]  # type: ignore[attr-defined]  # noqa: SLF001
    window.show_page("profiles")  # type: ignore[attr-defined]
    assert ctx.session.start()
    page.use(other.id)
    assert ctx.profiles.active().id != other.id
    assert "Pare as automações" in page._banner_text.text()  # noqa: SLF001
    card = next(c for c in page._cards if c.summary.profile.id == other.id)  # noqa: SLF001
    use_button = next(b for b in card.findChildren(QPushButton) if "Usar este perfil" in b.text())
    assert not use_button.isEnabled()  # o botão já avisa antes mesmo do clique
    ctx.session.stop()
    qtbot.waitUntil(lambda: any(
        "Usar este perfil" in b.text() and b.isEnabled()
        for c in page._cards for b in c.findChildren(QPushButton)  # noqa: SLF001
    ), timeout=2000)


def test_export_then_import_through_the_page(qtbot: QtBot, window: object, tmp_path: Path) -> None:
    ctx = window._ctx  # type: ignore[attr-defined]
    pid = ctx.profiles.active().id
    zone = ctx.zones.create(pid, ScreenRect(-9000, 0, 10, 10), "\\\\.\\DISPLAY9")  # fora das telas daqui
    ctx.rules.create(pid, "Exportada", zone.id, ColorCondition((9, 9, 9)), TriggerSettings(), ActionSpec())
    page = window._pages["profiles"]  # type: ignore[attr-defined]  # noqa: SLF001
    window.show_page("profiles")  # type: ignore[attr-defined]

    file = tmp_path / "perfil.vanthor.json"
    page.export_to(pid, file)
    assert file.exists() and '"format": "vanthor-bot-click/profile"' in file.read_text(encoding="utf-8")

    page.import_from(file)
    names = [c.summary.profile.name for c in page._cards]  # noqa: SLF001
    assert "Padrão 2" in names
    text = page._banner_text.text()  # noqa: SLF001
    assert "importado com 1 zona e 1 automação" in text and "fora das telas" in text
    imported = next(p for p in ctx.profiles.list() if p.name == "Padrão 2")
    assert [r.status for r in ctx.rules.list(imported.id)] == [RuleStatus.PAUSED]


def test_bad_file_shows_error_and_changes_nothing(qtbot: QtBot, window: object, tmp_path: Path) -> None:
    ctx = window._ctx  # type: ignore[attr-defined]
    page = window._pages["profiles"]  # type: ignore[attr-defined]  # noqa: SLF001
    bad = tmp_path / "ruim.json"
    bad.write_text('{"format": "vanthor-bot-click/profile", "version": 1, "zones": [{"ref": "z1"}]}', "utf-8")
    before = len(ctx.profiles.list())
    page.import_from(bad)
    assert len(ctx.profiles.list()) == before
    assert "Nada foi alterado" in page._banner_text.text()  # noqa: SLF001


def test_rename_active_profile_updates_sidebar(qtbot: QtBot, window: object) -> None:
    page = window._pages["profiles"]  # type: ignore[attr-defined]  # noqa: SLF001
    window.show_page("profiles")  # type: ignore[attr-defined]
    card = page._cards[0]  # noqa: SLF001
    card.name_edit.setText("Trabalho")
    page.rename(card)
    assert window._sidebar._profile.text().strip() == "Trabalho"  # noqa: SLF001
