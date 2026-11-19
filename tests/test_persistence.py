from __future__ import annotations

from pathlib import Path

import pytest

from vanthor.core.events import Event, EventKind, EventLevel
from vanthor.persistence.database import DEFAULT_PROFILE_NAME, Database, utc_now_iso
from vanthor.persistence.repositories import (
    ActivityRepository,
    AppSettings,
    ProfileRepository,
    RuleRepository,
    SettingsRepository,
)


def test_new_database_is_migrated_with_default_profile(db: Database) -> None:
    assert db.schema_version == 4
    assert [p.name for p in ProfileRepository(db).list()] == [DEFAULT_PROFILE_NAME]


def test_reopening_database_keeps_data_and_does_not_duplicate_profile(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    first = Database(path)
    SettingsRepository(first).save(AppSettings(emergency_hotkey="Ctrl+Shift+F12", start_with_windows=True))
    first.close()

    second = Database(path)
    assert SettingsRepository(second).load() == AppSettings("Ctrl+Shift+F12", True)
    assert len(ProfileRepository(second).list()) == 1
    second.close()


def test_database_from_newer_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    db = Database(path)
    db.execute("PRAGMA user_version = 99")
    db.close()
    with pytest.raises(RuntimeError, match="versão mais nova"):
        Database(path)


def test_settings_defaults_when_empty(db: Database) -> None:
    assert SettingsRepository(db).load() == AppSettings()


def test_corrupted_or_wrong_type_setting_falls_back_to_default(db: Database) -> None:
    db.execute("INSERT INTO settings (key, value) VALUES ('emergency_hotkey', '{quebrado')")
    db.execute("INSERT INTO settings (key, value) VALUES ('start_with_windows', '\"sim\"')")
    assert SettingsRepository(db).load() == AppSettings()


def test_rule_totals_are_real_counts(db: Database) -> None:
    profile = ProfileRepository(db).active()
    repo = RuleRepository(db)
    assert repo.totals(profile.id).rules == 0

    from vanthor.capture.geometry import ScreenRect
    from vanthor.persistence.repositories import ZoneRepository

    zone = ZoneRepository(db).create(profile.id, ScreenRect(0, 0, 5, 5), "m")
    now = utc_now_iso()
    # Só conta como ativa a regra ativa E com zona (sem zona ela não tem o que vigiar).
    for status, zone_id, detections, clicks in (
        ("active", zone.id, 5, 3), ("paused", zone.id, 2, 1), ("active", zone.id, 0, 0), ("active", None, 0, 0)
    ):
        db.execute(
            "INSERT INTO rules (profile_id, name, status, zone_id, config, detections_total, clicks_total, "
            "created_at, updated_at) VALUES (?, 'r', ?, ?, '{}', ?, ?, ?, ?)",
            (profile.id, status, zone_id, detections, clicks, now, now),
        )
    totals = repo.totals(profile.id)
    assert (totals.rules, totals.active, totals.detections, totals.clicks) == (4, 2, 7, 4)


def test_rule_status_is_constrained(db: Database) -> None:
    profile = ProfileRepository(db).active()
    now = utc_now_iso()
    with pytest.raises(Exception, match="CHECK"):
        db.execute(
            "INSERT INTO rules (profile_id, name, status, config, created_at, updated_at) "
            "VALUES (?, 'r', 'ligada', '{}', ?, ?)",
            (profile.id, now, now),
        )


def test_activity_log_roundtrip_newest_first(db: Database) -> None:
    repo = ActivityRepository(db)
    repo.append(Event(EventKind.ENGINE_STARTED, "Automações iniciadas"))
    repo.append(Event(EventKind.EMERGENCY_STOP, "Parada de emergência", EventLevel.WARNING))
    entries = repo.recent(10)
    assert [e.kind for e in entries] == [EventKind.EMERGENCY_STOP, EventKind.ENGINE_STARTED]
    assert entries[0].level is EventLevel.WARNING


def test_activity_log_prune_keeps_latest(db: Database) -> None:
    repo = ActivityRepository(db, keep_last=3)
    for i in range(10):
        repo.append(Event(EventKind.ENGINE_STARTED, f"evento {i}"))
    repo.prune()
    messages = [e.message for e in repo.recent(100)]
    assert messages == ["evento 9", "evento 8", "evento 7"]
