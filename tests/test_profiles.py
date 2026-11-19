"""Fase 7: perfis, duplicação e importação/exportação em JSON."""

from __future__ import annotations

import copy
import json

import pytest

from vanthor.actions.models import ActionKind, ActionSpec, ClickStep, KeyStep, MouseButton, WaitStep
from vanthor.capture.geometry import ScreenRect
from vanthor.persistence.database import DEFAULT_PROFILE_NAME, Database
from vanthor.persistence.profile_io import (
    MAX_FILE_BYTES,
    ProfileFileError,
    duplicate_profile,
    export_profile,
    import_profile,
    parse_profile_file,
    to_json,
)
from vanthor.persistence.repositories import ProfileError, ProfileRepository, RuleRepository, ZoneRepository
from vanthor.rules.models import FireMode, RuleStatus, TriggerSettings
from vanthor.vision.color import ColorCondition, ColorSpace, MatchMode


# -- perfis --------------------------------------------------------------------------------------
def test_create_rename_switch_and_delete(db: Database) -> None:
    profiles = ProfileRepository(db)
    work = profiles.create("  Trabalho   diário ")
    assert work.name == "Trabalho diário"
    profiles.set_active(work.id)
    assert profiles.active() == work
    assert profiles.rename(work.id, "Trabalho").name == "Trabalho"

    with pytest.raises(ProfileError, match="Troque"):
        profiles.delete(work.id)  # é o perfil em uso
    default = next(p for p in profiles.list() if p.name == DEFAULT_PROFILE_NAME)
    profiles.delete(default.id)
    # O último perfil é sempre o que está em uso: excluí-lo é sempre recusado.
    with pytest.raises(ProfileError):
        profiles.delete(work.id)
    assert [p.name for p in profiles.list()] == ["Trabalho"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 41, "padrão", "PADRÃO"])
def test_invalid_or_duplicate_names(db: Database, name: str) -> None:
    with pytest.raises(ProfileError):
        ProfileRepository(db).create(name)


def test_unique_name_adds_number(db: Database) -> None:
    profiles = ProfileRepository(db)
    assert profiles.unique_name("Novo") == "Novo"
    profiles.create("Novo")
    profiles.create("Novo 2")
    assert profiles.unique_name("novo") == "novo 3"
    assert len(profiles.unique_name("y" * 40)) <= 40


def test_zones_and_rules_belong_to_their_profile(db: Database) -> None:
    profiles, zones = ProfileRepository(db), ZoneRepository(db)
    other = profiles.create("Outro")
    zones.create(profiles.active().id, ScreenRect(0, 0, 5, 5), "m")
    assert zones.list(other.id) == []
    summaries = {s.profile.name: s for s in profiles.summaries()}
    assert (summaries[DEFAULT_PROFILE_NAME].zones, summaries["Outro"].zones) == (1, 0)


def test_deleting_profile_removes_its_zones_and_rules(db: Database) -> None:
    profiles, zones, rules = ProfileRepository(db), ZoneRepository(db), RuleRepository(db)
    temp = profiles.create("Temporário")
    zone = zones.create(temp.id, ScreenRect(0, 0, 5, 5), "m")
    rules.create(temp.id, "r", zone.id, ColorCondition((1, 2, 3)), TriggerSettings(), ActionSpec())
    profiles.delete(temp.id)
    assert zones.list(temp.id) == [] and rules.list(temp.id) == []


# -- exportar / importar --------------------------------------------------------------------------
@pytest.fixture
def rich_profile(db: Database) -> int:
    """Perfil com tudo que existe: zonas variadas, ajuste de cor, todos os tipos de ação e de disparo."""
    pid = ProfileRepository(db).active().id
    zones, rules = ZoneRepository(db), RuleRepository(db)
    a = zones.create(pid, ScreenRect(-2560, 100, 300, 50), "\\\\.\\DISPLAY2", 50)
    zones.update(a.with_changes(name="Botão comprar", enabled=False))
    zones.save_color_draft(a.id, ColorCondition((34, 197, 94), ColorSpace.HSV, 20, MatchMode.PERCENT, 3, 12.5))
    b = zones.create(pid, ScreenRect(10, 20, 30, 40), "\\\\.\\DISPLAY1", 1000)
    rules.create(pid, "Clique", a.id, ColorCondition((34, 197, 94)),
                 TriggerSettings(FireMode.ON_APPEAR, 3, 4, 250, 1500, 7),
                 ActionSpec(ActionKind.CLICK, (ClickStep(500, 400, MouseButton.RIGHT, True),), restore_cursor=False))
    rules.create(pid, "Sequência", b.id, ColorCondition((239, 68, 68), tolerance=30),
                 TriggerSettings(FireMode.CONTINUOUS, cooldown_ms=800),
                 ActionSpec(ActionKind.SEQUENCE, (ClickStep(1, 2), WaitStep(300), KeyStep("Ctrl+S"))),
                 RuleStatus.PAUSED)
    rules.create(pid, "Registro", b.id, ColorCondition((0, 0, 0)), TriggerSettings(FireMode.ONCE), ActionSpec())
    return pid


def _content(data: dict[str, object]) -> dict[str, object]:
    """O que importa comparar: tudo, menos metadados do arquivo, nome do perfil e status (pausados na importação)."""
    clean = copy.deepcopy(data)
    for key in ("exported_at", "profile", "app_version"):
        clean.pop(key, None)
    for rule in clean["rules"]:  # type: ignore[union-attr]
        rule.pop("status")  # type: ignore[union-attr]
    return clean


def test_export_import_roundtrip_preserves_everything(db: Database, rich_profile: int) -> None:
    original = export_profile(db, rich_profile)
    raw = to_json(original).encode("utf-8")
    report = import_profile(db, parse_profile_file(raw))
    assert (report.zones, report.rules) == (2, 3)
    assert report.profile.name == f"{DEFAULT_PROFILE_NAME} 2"
    reexported = export_profile(db, report.profile.id)
    assert _content(reexported) == _content(original)


def test_imported_rules_arrive_paused_for_review(db: Database, rich_profile: int) -> None:
    report = import_profile(db, export_profile(db, rich_profile))
    statuses = {r.status for r in RuleRepository(db).list(report.profile.id)}
    assert statuses == {RuleStatus.PAUSED}


def test_duplicate_keeps_statuses_and_remaps_zones(db: Database, rich_profile: int) -> None:
    copy_profile = duplicate_profile(db, rich_profile)
    assert copy_profile.name == f"{DEFAULT_PROFILE_NAME} (cópia)"
    original_rules = RuleRepository(db).list(rich_profile)
    copied_rules = RuleRepository(db).list(copy_profile.id)
    assert [r.status for r in copied_rules] == [r.status for r in original_rules]
    copied_zone_ids = {z.id for z in ZoneRepository(db).list(copy_profile.id)}
    assert {r.zone_id for r in copied_rules} <= copied_zone_ids  # aponta para as zonas da CÓPIA
    assert not copied_zone_ids & {z.id for z in ZoneRepository(db).list(rich_profile)}


def test_zoneless_rule_survives_roundtrip(db: Database) -> None:
    pid = ProfileRepository(db).active().id
    zone = ZoneRepository(db).create(pid, ScreenRect(0, 0, 5, 5), "m")
    rule = RuleRepository(db).create(pid, "Órfã", zone.id, ColorCondition((1, 1, 1)), TriggerSettings(), ActionSpec())
    ZoneRepository(db).delete(zone.id)
    data = export_profile(db, pid)
    assert data["rules"][0]["zone"] is None
    report = import_profile(db, data)
    imported = RuleRepository(db).list(report.profile.id)
    assert [(r.name, r.zone_id) for r in imported] == [("Órfã", None)]
    del rule


# -- arquivos ruins -------------------------------------------------------------------------------
def test_rejects_non_profile_files() -> None:
    with pytest.raises(ProfileFileError, match="JSON"):
        parse_profile_file(b"\x00\xff nao e json")
    with pytest.raises(ProfileFileError, match="não é um perfil"):
        parse_profile_file(b'{"format": "outro"}')
    with pytest.raises(ProfileFileError, match="versão"):
        parse_profile_file(b'{"format": "vanthor-bot-click/profile", "version": 99}')
    with pytest.raises(ProfileFileError, match="grande"):
        parse_profile_file(b" " * (MAX_FILE_BYTES + 1))


def test_accepts_utf8_bom(db: Database, rich_profile: int) -> None:
    raw = "﻿".encode() + to_json(export_profile(db, rich_profile)).encode("utf-8")
    assert parse_profile_file(raw)["format"] == "vanthor-bot-click/profile"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["zones"][0]["rect"].update(width=0), "rect.width"),
        (lambda d: d["zones"][0]["rect"].update(x="10"), "rect.x"),
        (lambda d: d["zones"][0].update(interval_ms=1), "interval_ms"),
        (lambda d: d["zones"][0].update(enabled="sim"), "enabled"),
        (lambda d: d["zones"][0].update(name="n" * 61), "longo demais"),
        (lambda d: d["zones"][1].update(ref=d["zones"][0]["ref"]), "mesma referência"),
        (lambda d: d["rules"][0].update(zone="z99"), "zona que não está"),
        (lambda d: d["rules"][0]["condition"].update(tolerance=999), "automações\\[1\\]"),
        (lambda d: d["rules"][0]["action"].update(kind="formatar_disco"), "automações\\[1\\]"),
        (lambda d: d["rules"][0]["action"]["steps"][0].update(type="teleport"), "automações\\[1\\]"),
        (lambda d: d["rules"][1]["trigger"].update(cooldown_ms=10), "automações\\[2\\]"),
        (lambda d: d.update(zones="tudo"), "zones precisa ser uma lista"),
        (lambda d: d.update(rules=[{}] * 501), "máximo é 500"),
    ],
)
def test_invalid_content_is_rejected_with_location(db: Database, rich_profile: int, mutate, message: str) -> None:  # type: ignore[no-untyped-def]
    data = export_profile(db, rich_profile)
    mutate(data)
    before = len(ProfileRepository(db).list())
    with pytest.raises(ProfileFileError, match=message):
        import_profile(db, data)
    assert len(ProfileRepository(db).list()) == before  # nada foi criado


def test_import_is_all_or_nothing_on_database_failure(db: Database, rich_profile: int,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    data = export_profile(db, rich_profile)
    calls = {"n": 0}
    real_create = RuleRepository.create

    def failing_create(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disco cheio (simulado)")
        return real_create(self, *args, **kwargs)

    monkeypatch.setattr(RuleRepository, "create", failing_create)
    profiles_before = len(ProfileRepository(db).list())
    zones_before = db.query_one("SELECT COUNT(*) FROM zones")[0]
    with pytest.raises(RuntimeError):
        import_profile(db, data)
    assert len(ProfileRepository(db).list()) == profiles_before
    assert db.query_one("SELECT COUNT(*) FROM zones")[0] == zones_before


def test_bad_zone_color_is_replaced_not_fatal(db: Database, rich_profile: int) -> None:
    data = export_profile(db, rich_profile)
    data["zones"][0]["color"] = "javascript:alert(1)"
    report = import_profile(db, data)
    zone = ZoneRepository(db).list(report.profile.id)[0]
    assert zone.color.startswith("#") and len(zone.color) == 7


def test_export_is_plain_json_without_internal_ids(db: Database, rich_profile: int) -> None:
    text = to_json(export_profile(db, rich_profile))
    data = json.loads(text)
    assert all(z["ref"].startswith("z") for z in data["zones"])
    assert "id" not in data["zones"][0] and "profile_id" not in data["rules"][0]
