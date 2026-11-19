"""Motor de regras: várias regras simultâneas e independentes sobre quadros das zonas."""

from __future__ import annotations

import numpy as np
import pytest

from vanthor.capture.monitor import Frame
from vanthor.core.events import EventBus, EventKind
from vanthor.persistence.database import Database
from vanthor.persistence.repositories import ProfileRepository, RuleRepository, ZoneRepository
from vanthor.rules.engine import LogOnlyExecutor, RuleEngine
from vanthor.rules.models import ActionSpec, FireMode, Rule, RuleStatus, TriggerSettings
from vanthor.rules.runtime import RulePhase
from vanthor.vision.color import ColorCondition

from .conftest import Recorder

GREEN, RED, BLUE, BLACK = (34, 197, 94), (239, 68, 68), (59, 130, 246), (0, 0, 0)


def frame_of(rgb: tuple[int, int, int], t: float) -> Frame:
    image = np.zeros((4, 4, 3), np.uint8)
    image[:, :] = rgb
    return Frame(image, t, 0.1)


def rule(rid: int, zone: int, color: tuple[int, int, int], **trigger: object) -> Rule:
    settings: dict[str, object] = {"confirm_frames": 1, "cooldown_ms": 0}
    settings.update(trigger)
    return Rule(
        rid, 1, f"Regra {rid}", RuleStatus.ACTIVE, zone, ColorCondition(color, tolerance=10),
        TriggerSettings(**settings),  # type: ignore[arg-type]
    )


class Screen:
    """Simula as zonas: cada chamada a ``show`` gera um quadro novo para a zona."""

    def __init__(self) -> None:
        self.frames: dict[int, Frame] = {}
        self.t = 0.0

    def show(self, **zones: tuple[int, int, int]) -> None:
        self.t += 0.1
        for name, rgb in zones.items():
            self.frames[int(name[1:])] = frame_of(rgb, self.t)

    def __call__(self, zone_id: int) -> Frame | None:
        return self.frames.get(zone_id)


class Counts:
    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, int]]] = []

    def __call__(self, rule_id: int, **kw: int) -> None:
        self.calls.append((rule_id, kw))

    def total(self, rule_id: int, field: str) -> int:
        return sum(kw.get(field, 0) for rid, kw in self.calls if rid == rule_id)


@pytest.fixture
def setup(bus: EventBus) -> tuple[RuleEngine, Screen, Counts]:
    screen, counts = Screen(), Counts()
    return RuleEngine(bus, screen, LogOnlyExecutor(bus), counts), screen, counts


def test_project_example_three_independent_rules(setup: tuple[RuleEngine, Screen, Counts], recorder: Recorder) -> None:
    """Regra A: verde na zona 1. Regra B: vermelho na zona 2. Regra C: azul na zona 3."""
    engine, screen, counts = setup
    engine.set_rules([rule(1, 1, GREEN), rule(2, 2, RED), rule(3, 3, BLUE)], {1: "Z1", 2: "Z2", 3: "Z3"})

    screen.show(z1=BLACK, z2=BLACK, z3=BLACK)
    engine(screen.t)
    screen.show(z1=GREEN, z2=BLACK, z3=BLUE)
    engine(screen.t)
    assert [counts.total(i, "executions") for i in (1, 2, 3)] == [1, 0, 1]
    screen.show(z1=GREEN, z2=RED, z3=BLUE)
    engine(screen.t)
    assert [counts.total(i, "executions") for i in (1, 2, 3)] == [1, 1, 1]
    actions = [e for e in recorder.events if e.kind is EventKind.ACTION]
    assert sorted(e.rule_id for e in actions) == [1, 2, 3]


def test_same_zone_different_colors(setup: tuple[RuleEngine, Screen, Counts]) -> None:
    engine, screen, counts = setup
    engine.set_rules([rule(1, 1, GREEN), rule(2, 1, RED)], {1: "Z1"})
    screen.show(z1=GREEN)
    engine(screen.t)
    screen.show(z1=RED)
    engine(screen.t)
    assert (counts.total(1, "executions"), counts.total(2, "executions")) == (1, 1)


def test_rule_only_reacts_to_new_frames(setup: tuple[RuleEngine, Screen, Counts]) -> None:
    """Zona lenta (1×/s) com motor rápido: o mesmo quadro não conta duas vezes para confirmar."""
    engine, screen, counts = setup
    engine.set_rules([rule(1, 1, GREEN, confirm_frames=3)], {1: "Z1"})
    screen.show(z1=GREEN)
    for i in range(10):
        engine(screen.t + i * 0.05)
    assert counts.total(1, "executions") == 0
    assert engine.live()[1].phase is RulePhase.CONFIRMING


def test_detection_recorded_once_per_appearance(setup: tuple[RuleEngine, Screen, Counts], recorder: Recorder) -> None:
    engine, screen, counts = setup
    engine.set_rules([rule(1, 1, GREEN)], {1: "Z1"})
    for _ in range(5):
        screen.show(z1=GREEN)
        engine(screen.t)
    assert counts.total(1, "detections") == 1
    assert recorder.kinds.count("detection") == 1
    assert "Regra 1: cor detectada em Z1" in [e.message for e in recorder.events]


def test_delay_fires_on_poll_without_new_frame(setup: tuple[RuleEngine, Screen, Counts]) -> None:
    engine, screen, counts = setup
    engine.set_rules([rule(1, 1, GREEN, delay_ms=300)], {1: "Z1"})
    screen.show(z1=GREEN)
    engine(screen.t)
    assert counts.total(1, "executions") == 0
    engine(screen.t + 0.31)  # nenhum quadro novo, mas a espera venceu
    assert counts.total(1, "executions") == 1


def test_paused_and_zoneless_rules_do_not_run(setup: tuple[RuleEngine, Screen, Counts]) -> None:
    engine, screen, counts = setup
    paused = rule(1, 1, GREEN).with_changes(status=RuleStatus.PAUSED)
    zoneless = rule(2, 1, GREEN).with_changes(zone_id=None)
    engine.set_rules([paused, zoneless], {1: "Z1"})
    screen.show(z1=GREEN)
    engine(screen.t)
    assert counts.calls == []
    assert engine.live() == {}


def test_unrelated_edit_keeps_rule_state(setup: tuple[RuleEngine, Screen, Counts]) -> None:
    engine, screen, counts = setup
    a, b = rule(1, 1, GREEN), rule(2, 2, RED)
    engine.set_rules([a, b], {1: "Z1", 2: "Z2"})
    screen.show(z1=GREEN, z2=BLACK)
    engine(screen.t)
    # Renomear não muda comportamento: a regra 1 continua "já agiu nesta aparição".
    engine.set_rules([a.with_changes(name="Novo nome"), b], {1: "Z1", 2: "Z2"})
    screen.show(z1=GREEN, z2=BLACK)
    engine(screen.t)
    assert counts.total(1, "executions") == 1


def test_changed_condition_restarts_rule(setup: tuple[RuleEngine, Screen, Counts]) -> None:
    engine, screen, counts = setup
    a = rule(1, 1, GREEN)
    engine.set_rules([a], {1: "Z1"})
    screen.show(z1=GREEN)
    engine(screen.t)
    engine.set_rules([a.with_changes(trigger=TriggerSettings(confirm_frames=1, cooldown_ms=0, delay_ms=0,
                                                              mode=FireMode.ON_APPEAR, release_frames=3))], {1: "Z1"})
    screen.show(z1=GREEN)
    engine(screen.t)
    assert counts.total(1, "executions") == 2


def test_reset_rearms_once_mode(setup: tuple[RuleEngine, Screen, Counts]) -> None:
    engine, screen, counts = setup
    engine.set_rules([rule(1, 1, GREEN, mode=FireMode.ONCE)], {1: "Z1"})
    screen.show(z1=GREEN)
    engine(screen.t)
    engine.reset()  # parar e iniciar a automação de novo
    screen.show(z1=GREEN)
    engine(screen.t)
    assert counts.total(1, "executions") == 2


def test_limit_publishes_notice(setup: tuple[RuleEngine, Screen, Counts], recorder: Recorder) -> None:
    engine, screen, _ = setup
    engine.set_rules([rule(1, 1, GREEN, max_executions=1)], {1: "Z1"})
    screen.show(z1=GREEN)
    engine(screen.t)
    assert recorder.events[-1].kind is EventKind.NOTICE
    assert "limite de 1 execuções" in recorder.events[-1].message


def test_shared_zone_and_condition_detected_once(bus: EventBus, monkeypatch: pytest.MonkeyPatch) -> None:
    import vanthor.rules.engine as engine_module

    calls: list[int] = []
    real = engine_module.detect

    def counting(image, condition):  # type: ignore[no-untyped-def]
        calls.append(1)
        return real(image, condition)

    monkeypatch.setattr(engine_module, "detect", counting)
    screen = Screen()
    engine = RuleEngine(bus, screen, LogOnlyExecutor(bus), Counts())
    engine.set_rules([rule(1, 1, GREEN), rule(2, 1, GREEN), rule(3, 1, RED)], {1: "Z1"})
    screen.show(z1=GREEN)
    engine(screen.t)
    assert len(calls) == 2  # verde (compartilhado por 2 regras) + vermelho


# -- repositório ------------------------------------------------------------------------------------
def test_rule_repository_crud_counters_and_zone_deletion(db: Database) -> None:
    from vanthor.capture.geometry import ScreenRect

    profile = ProfileRepository(db).active()
    zone = ZoneRepository(db).create(profile.id, ScreenRect(0, 0, 10, 10), "m")
    repo = RuleRepository(db)
    created = repo.create(profile.id, "  Comprar  ", zone.id, ColorCondition(GREEN), TriggerSettings(), ActionSpec())
    assert created.name == "Comprar" and created.status is RuleStatus.ACTIVE and created.zone_id == zone.id

    repo.record(created.id, detections=1)
    repo.record(created.id, executions=1, clicks=2)
    again = repo.get(created.id)
    assert again is not None and (again.detections_total, again.executions_total) == (1, 1)
    assert repo.last_fired_at(created.id) is not None
    totals = repo.totals(profile.id)
    assert (totals.rules, totals.active, totals.detections, totals.clicks) == (1, 1, 1, 2)

    copy = repo.duplicate(created.id)
    assert copy.name == "Comprar (cópia)" and copy.status is RuleStatus.PAUSED

    ZoneRepository(db).delete(zone.id)
    orphan = repo.get(created.id)
    assert orphan is not None and orphan.zone_id is None and not orphan.runnable
    assert repo.totals(profile.id).active == 0


def test_rule_repository_rejects_empty_name(db: Database) -> None:
    from vanthor.capture.geometry import ScreenRect

    profile = ProfileRepository(db).active()
    zone = ZoneRepository(db).create(profile.id, ScreenRect(0, 0, 10, 10), "m")
    with pytest.raises(ValueError):
        RuleRepository(db).create(profile.id, "   ", zone.id, ColorCondition(GREEN), TriggerSettings(), ActionSpec())


def test_corrupted_rule_is_skipped_not_fatal(db: Database) -> None:
    from vanthor.persistence.database import utc_now_iso

    profile = ProfileRepository(db).active()
    now = utc_now_iso()
    db.execute(
        "INSERT INTO rules (profile_id, name, status, config, created_at, updated_at) VALUES (?, 'x', 'active', "
        "'{quebrado', ?, ?)",
        (profile.id, now, now),
    )
    assert RuleRepository(db).list(profile.id) == []
