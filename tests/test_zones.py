from __future__ import annotations

import pytest

from vanthor.capture.capturer import CaptureError
from vanthor.capture.geometry import ScreenRect
from vanthor.capture.monitor import ZoneMonitor
from vanthor.capture.scheduler import CaptureScheduler
from vanthor.capture.zones import ZONE_COLORS, Zone, interval_label, next_zone_color, next_zone_name
from vanthor.core.events import EventBus, EventKind
from vanthor.persistence.database import Database
from vanthor.persistence.repositories import ProfileRepository, ZoneRepository

import numpy as np

from .conftest import Recorder


def make_zone(zone_id: int, rect: ScreenRect, interval_ms: int = 100, enabled: bool = True) -> Zone:
    return Zone(zone_id, 1, f"Zona {zone_id:02d}", ZONE_COLORS[0], rect, "\\\\.\\DISPLAY1", interval_ms, enabled)


# -- nomes, cores e rótulos ------------------------------------------------------------------
def test_next_zone_name_fills_gaps() -> None:
    assert next_zone_name([]) == "Zona 01"
    assert next_zone_name(["Zona 01", "Zona 03"]) == "Zona 02"
    assert next_zone_name(["Caixa", "Zona 01"]) == "Zona 02"


def test_next_zone_color_prefers_least_used() -> None:
    assert next_zone_color([]) == ZONE_COLORS[0]
    assert next_zone_color([ZONE_COLORS[0]]) == ZONE_COLORS[1]
    assert next_zone_color(list(ZONE_COLORS)) == ZONE_COLORS[0]


@pytest.mark.parametrize(
    ("ms", "label"),
    [(50, "20× por segundo"), (100, "10× por segundo"), (250, "4× por segundo"), (1000, "1× por segundo"),
     (2000, "A cada 2 s"), (1500, "A cada 1,5 s")],
)
def test_interval_label(ms: int, label: str) -> None:
    assert interval_label(ms) == label


# -- agendador ---------------------------------------------------------------------------------
def test_scheduler_respects_each_zone_interval() -> None:
    scheduler = CaptureScheduler()
    fast = make_zone(1, ScreenRect(0, 0, 10, 10), interval_ms=100)
    slow = make_zone(2, ScreenRect(20, 0, 10, 10), interval_ms=500)
    scheduler.set_zones([fast, slow])

    def ids(now: float) -> list[int]:
        return sorted(i for g in scheduler.due(now) for i in g.zone_ids)

    assert ids(0.0) == [1, 2]  # primeira volta captura tudo
    assert ids(0.05) == []
    assert ids(0.10) == [1]
    assert ids(0.20) == [1]
    assert ids(0.50) == [1, 2]


def test_scheduler_skips_disabled_zones() -> None:
    scheduler = CaptureScheduler()
    scheduler.set_zones([make_zone(1, ScreenRect(0, 0, 5, 5), enabled=False)])
    assert scheduler.due(0) == []
    assert scheduler.zone_count == 0


def test_scheduler_captures_identical_rects_once() -> None:
    scheduler = CaptureScheduler()
    rect = ScreenRect(0, 0, 10, 10)
    scheduler.set_zones([make_zone(1, rect), make_zone(2, rect), make_zone(3, ScreenRect(50, 50, 4, 4))])
    groups = scheduler.due(0)
    assert len(groups) == 2
    assert next(g for g in groups if g.rect == rect).zone_ids == (1, 2)


def test_scheduler_keeps_rhythm_when_zones_are_updated() -> None:
    scheduler = CaptureScheduler()
    zone = make_zone(1, ScreenRect(0, 0, 5, 5), interval_ms=1000)
    scheduler.set_zones([zone])
    assert scheduler.due(0.0)
    scheduler.set_zones([zone, make_zone(2, ScreenRect(9, 9, 5, 5))])
    assert [g.zone_ids for g in scheduler.due(0.1)] == [(2,)]  # a zona 1 não dispara antes da hora


# -- monitor de zonas (com capturador falso) ----------------------------------------------------
class FakeCapturer:
    def __init__(self, fail_on: set[ScreenRect] | None = None) -> None:
        self.grabs: list[ScreenRect] = []
        self.fail_on = fail_on or set()
        self.closed = False

    def grab(self, rect: ScreenRect) -> np.ndarray:
        self.grabs.append(rect)
        if rect in self.fail_on:
            raise CaptureError("tela bloqueada")
        return np.zeros((rect.height, rect.width, 3), np.uint8)

    def close(self) -> None:
        self.closed = True


SCREEN = ScreenRect(0, 0, 1920, 1080)


def test_monitor_captures_only_zone_rects_and_stores_frames(bus: EventBus) -> None:
    fake = FakeCapturer()
    monitor = ZoneMonitor(bus, lambda: [SCREEN], lambda: fake)  # type: ignore[arg-type, return-value]
    zone = make_zone(1, ScreenRect(100, 200, 30, 20))
    monitor.set_zones([zone])
    monitor(0.0)
    assert fake.grabs == [zone.rect]
    frame = monitor.latest_frame(1)
    assert frame is not None and frame.image.shape == (20, 30, 3)
    assert monitor.status(1).captures == 1


def test_monitor_flags_offscreen_zone_once(bus: EventBus, recorder: Recorder) -> None:
    fake = FakeCapturer()
    monitor = ZoneMonitor(bus, lambda: [SCREEN], lambda: fake)  # type: ignore[arg-type, return-value]
    monitor.set_zones([make_zone(1, ScreenRect(-500, 0, 10, 10), interval_ms=50)])
    for t in (0.0, 0.05, 0.10, 0.15):
        monitor(t)
    assert fake.grabs == []
    assert recorder.kinds.count("error") == 1
    assert monitor.status(1).error == "A zona está fora das telas conectadas"


def test_monitor_recovers_after_capture_failure(bus: EventBus, recorder: Recorder) -> None:
    rect = ScreenRect(0, 0, 10, 10)
    fake = FakeCapturer(fail_on={rect})
    monitor = ZoneMonitor(bus, lambda: [SCREEN], lambda: fake)  # type: ignore[arg-type, return-value]
    monitor.set_zones([make_zone(1, rect, interval_ms=50)])
    monitor(0.0)
    monitor(0.05)
    assert recorder.kinds.count("error") == 1
    fake.fail_on.clear()
    monitor(0.10)
    assert monitor.status(1).error is None
    assert recorder.events[-1].kind is EventKind.NOTICE


def test_monitor_release_closes_capturer(bus: EventBus) -> None:
    fake = FakeCapturer()
    monitor = ZoneMonitor(bus, lambda: [SCREEN], lambda: fake)  # type: ignore[arg-type, return-value]
    monitor.set_zones([make_zone(1, ScreenRect(0, 0, 5, 5))])
    monitor(0.0)
    monitor.release()
    assert fake.closed
    assert monitor.latest_frame(1) is None


# -- repositório -------------------------------------------------------------------------------
def test_zone_repository_crud(db: Database) -> None:
    profile = ProfileRepository(db).active()
    repo = ZoneRepository(db)
    first = repo.create(profile.id, ScreenRect(-100, 50, 40, 30), "\\\\.\\DISPLAY2")
    second = repo.create(profile.id, ScreenRect(0, 0, 10, 10), "\\\\.\\DISPLAY1", interval_ms=500)
    assert (first.name, second.name) == ("Zona 01", "Zona 02")
    assert first.color != second.color
    assert first.rect == ScreenRect(-100, 50, 40, 30)  # coordenadas negativas preservadas

    repo.update(first.with_changes(name="  Botão verde ", enabled=False, rect=ScreenRect(1, 2, 3, 4)))
    reloaded = repo.get(first.id)
    assert reloaded is not None
    assert (reloaded.name, reloaded.enabled, reloaded.rect) == ("Botão verde", False, ScreenRect(1, 2, 3, 4))

    repo.delete(second.id)
    assert [z.id for z in repo.list(profile.id)] == [first.id]


def test_zone_repository_validates(db: Database) -> None:
    profile = ProfileRepository(db).active()
    repo = ZoneRepository(db)
    zone = repo.create(profile.id, ScreenRect(0, 0, 10, 10), "x")
    with pytest.raises(ValueError):
        repo.update(zone.with_changes(name="   "))
    with pytest.raises(ValueError):
        repo.update(zone.with_changes(interval_ms=5))


def test_zones_are_deleted_with_profile(db: Database) -> None:
    profile = ProfileRepository(db).active()
    repo = ZoneRepository(db)
    repo.create(profile.id, ScreenRect(0, 0, 10, 10), "x")
    db.execute("DELETE FROM profiles WHERE id = ?", (profile.id,))
    assert repo.list(profile.id) == []


# -- ajuste de cor salvo por zona (Fase 3) -----------------------------------------------------
def test_color_draft_roundtrip_and_corruption(db: Database) -> None:
    from vanthor.vision.color import ColorCondition, ColorSpace

    profile = ProfileRepository(db).active()
    repo = ZoneRepository(db)
    zone = repo.create(profile.id, ScreenRect(0, 0, 10, 10), "x")
    assert repo.color_draft(zone.id) is None
    condition = ColorCondition((34, 197, 94), ColorSpace.HSV, 20)
    repo.save_color_draft(zone.id, condition)
    assert repo.color_draft(zone.id) == condition
    db.execute("UPDATE zones SET color_draft = '{\"color\": 5}' WHERE id = ?", (zone.id,))
    assert repo.color_draft(zone.id) is None  # corrompido: ignora em vez de quebrar a tela


def test_upgrade_from_phase2_database_keeps_zones(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import sqlite3

    from vanthor.persistence import database as dbmod

    path = tmp_path / "v2.db"
    conn = sqlite3.connect(path)
    for version, script in enumerate(dbmod._MIGRATIONS[:2], start=1):  # noqa: SLF001
        conn.executescript(f"{script}\nPRAGMA user_version = {version};")
    now = dbmod.utc_now_iso()
    conn.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('Padrão', ?, ?)", (now, now))
    conn.execute(
        "INSERT INTO zones (profile_id, name, color, x, y, width, height, monitor, interval_ms, created_at, "
        "updated_at) VALUES (1, 'Antiga', '#34D399', 1, 2, 3, 4, 'm', 100, ?, ?)",
        (now, now),
    )
    conn.commit()
    conn.close()

    upgraded = Database(path)
    assert upgraded.schema_version == len(dbmod._MIGRATIONS)  # noqa: SLF001
    zones = ZoneRepository(upgraded).list(1)
    assert [z.name for z in zones] == ["Antiga"]
    assert ZoneRepository(upgraded).color_draft(zones[0].id) is None
    upgraded.close()


# -- capturas agrupadas (otimização da Fase 8) ------------------------------------------------------
def test_plan_batches_merges_nearby_zones_on_same_monitor() -> None:
    from vanthor.capture.scheduler import CaptureGroup, plan_batches

    left_monitor = ScreenRect(-1920, 0, 1920, 1080)
    groups = [
        CaptureGroup(ScreenRect(100, 200, 200, 120), (1,)),
        CaptureGroup(ScreenRect(320, 200, 200, 120), (2,)),
        CaptureGroup(ScreenRect(540, 210, 200, 120), (3,)),
        CaptureGroup(ScreenRect(-500, 200, 50, 50), (4,)),  # outro monitor: nunca junta
    ]
    batches = plan_batches(groups, [SCREEN, left_monitor])
    assert len(batches) == 2
    main = next(b for b in batches if b.rect.x >= 0)
    assert main.rect == ScreenRect(100, 200, 640, 130)
    assert sorted(i for g in main.groups for i in g.zone_ids) == [1, 2, 3]


def test_plan_batches_keeps_distant_zones_apart() -> None:
    from vanthor.capture.scheduler import CaptureGroup, plan_batches

    corners = [CaptureGroup(ScreenRect(0, 0, 20, 20), (1,)), CaptureGroup(ScreenRect(1880, 1040, 20, 20), (2,))]
    assert len(plan_batches(corners, [SCREEN])) == 2  # juntar capturaria a tela inteira


def test_monitor_crops_each_zone_from_shared_capture(bus: EventBus) -> None:
    class PatternCapturer(FakeCapturer):
        def grab(self, rect: ScreenRect) -> np.ndarray:
            self.grabs.append(rect)
            ys, xs = np.mgrid[rect.y : rect.bottom, rect.x : rect.right]
            img = np.zeros((rect.height, rect.width, 3), np.uint8)
            img[..., 0], img[..., 1] = xs % 256, ys % 256  # cada pixel "sabe" onde está
            return img

    fake = PatternCapturer()
    monitor = ZoneMonitor(bus, lambda: [SCREEN], lambda: fake)  # type: ignore[arg-type, return-value]
    a, b = make_zone(1, ScreenRect(100, 50, 30, 20)), make_zone(2, ScreenRect(150, 60, 10, 10))
    monitor.set_zones([a, b])
    monitor(0.0)
    assert len(fake.grabs) == 1  # uma captura só para as duas zonas
    for zone in (a, b):
        frame = monitor.latest_frame(zone.id)
        assert frame is not None and frame.image.shape == (zone.rect.height, zone.rect.width, 3)
        assert frame.image[0, 0, 0] == zone.rect.x % 256 and frame.image[0, 0, 1] == zone.rect.y % 256
