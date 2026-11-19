"""Avaliador de regra: decide QUANDO disparar a partir da sequência de quadros."""

from __future__ import annotations

import pytest

from vanthor.rules.models import (
    ActionKind,
    ActionSpec,
    FireMode,
    TriggerSettings,
    parse_config,
)
from vanthor.rules.runtime import RulePhase, RuleRuntime, RuleSignal
from vanthor.vision.color import ColorCondition

S = RuleSignal


class Feed:
    """Alimenta o avaliador com quadros a cada 100 ms e coleta os sinais."""

    def __init__(self, trigger: TriggerSettings, start: float = 0.0, step: float = 0.1) -> None:
        self.rt = RuleRuntime(trigger)
        self.now = start
        self.step = step
        self.signals: list[RuleSignal] = []

    def frames(self, pattern: str) -> list[RuleSignal]:
        """'1' = cor presente, '0' = ausente. Devolve os sinais emitidos neste trecho."""
        out: list[RuleSignal] = []
        for ch in pattern:
            out += self.rt.on_frame(self.now, ch == "1")
            self.now += self.step
        self.signals += out
        return out

    def wait(self, seconds: float) -> list[RuleSignal]:
        self.now += seconds
        out = self.rt.poll(self.now)
        self.signals += out
        return out

    @property
    def fires(self) -> int:
        return self.signals.count(S.FIRE)


def trig(**kw: object) -> TriggerSettings:
    base: dict[str, object] = {"cooldown_ms": 0}
    base.update(kw)
    return TriggerSettings(**base)  # type: ignore[arg-type]


# -- confirmação por quadros seguidos ---------------------------------------------------------------
def test_needs_consecutive_frames_to_confirm() -> None:
    f = Feed(trig(confirm_frames=2))
    assert f.frames("1") == []
    assert f.rt.phase is RulePhase.CONFIRMING
    assert f.frames("1") == [S.APPEARED, S.FIRE]


def test_single_frame_confirmation() -> None:
    assert Feed(trig(confirm_frames=1)).frames("1") == [S.APPEARED, S.FIRE]


def test_flicker_never_confirms() -> None:
    f = Feed(trig(confirm_frames=2))
    f.frames("1010101010")
    assert f.fires == 0


# -- disparo na transição ------------------------------------------------------------------------------
def test_on_appear_fires_once_while_color_stays() -> None:
    f = Feed(trig())
    f.frames("1" * 50)
    assert f.fires == 1
    assert f.rt.phase is RulePhase.PRESENT


def test_brief_dropout_does_not_rearm_debounce() -> None:
    f = Feed(trig(release_frames=2))
    f.frames("11")
    f.frames("1101111")  # um quadro sem a cor no meio: não conta como "sumiu"
    assert f.fires == 1


def test_rearms_after_color_really_disappears() -> None:
    f = Feed(trig(release_frames=2))
    f.frames("11")
    assert f.frames("00") == [S.DISAPPEARED]
    assert f.rt.phase is RulePhase.WAITING
    f.frames("11")
    assert f.fires == 2


# -- espera antes de agir ------------------------------------------------------------------------------
def test_delay_postpones_action() -> None:
    f = Feed(trig(delay_ms=500))
    assert f.frames("11") == [S.APPEARED]
    assert f.rt.phase is RulePhase.SCHEDULED
    assert f.wait(0.3) == []
    assert f.wait(0.25) == [S.FIRE]


def test_delay_is_cancelled_if_color_leaves() -> None:
    f = Feed(trig(delay_ms=1000))
    f.frames("11")
    assert f.frames("00") == [S.DELAY_CANCELLED, S.DISAPPEARED]
    f.wait(5)
    assert f.fires == 0


# -- intervalo mínimo (cooldown) ---------------------------------------------------------------------------
def test_cooldown_postpones_reappearance_until_allowed() -> None:
    f = Feed(trig(cooldown_ms=1000))
    f.frames("11")  # dispara em t=0,1
    f.frames("0011")  # some e volta em t=0,5 (antes de 1 s); relógio agora em 0,6
    assert f.fires == 1
    assert f.rt.phase is RulePhase.COOLDOWN
    assert f.wait(0.3) == []  # t=0,9: ainda no intervalo mínimo (libera em 1,1)
    f.wait(0.3)  # t=1,2: intervalo cumprido e a cor continua lá
    assert f.fires == 2


def test_cooldown_wait_cancelled_if_color_leaves() -> None:
    f = Feed(trig(cooldown_ms=2000))
    f.frames("11")
    f.frames("0011")
    f.frames("00")
    f.wait(5)
    assert f.fires == 1


# -- modos ------------------------------------------------------------------------------------------------
def test_continuous_repeats_every_cooldown_while_present() -> None:
    f = Feed(trig(mode=FireMode.CONTINUOUS, cooldown_ms=300))
    f.frames("1" * 12)  # 1,2 s com a cor: dispara em ~0,1 / 0,4 / 0,7 / 1,0
    assert f.fires == 4
    f.frames("00")
    f.wait(2)
    assert f.fires == 4


def test_once_fires_a_single_time() -> None:
    f = Feed(trig(mode=FireMode.ONCE))
    f.frames("11")
    f.frames("0011001111")
    assert f.fires == 1
    assert f.rt.phase is RulePhase.DONE


def test_execution_limit() -> None:
    f = Feed(trig(max_executions=2))
    f.frames("11")
    f.frames("0011")
    assert f.signals[-2:] == [S.FIRE, S.LIMIT_REACHED]
    f.frames("0011" * 3)
    assert f.fires == 2
    assert f.rt.phase is RulePhase.DONE
    assert f.rt.executions == 2


def test_continuous_limit_stops_repetition() -> None:
    f = Feed(trig(mode=FireMode.CONTINUOUS, cooldown_ms=100, max_executions=3))
    f.frames("1" * 30)
    assert f.fires == 3


def test_poll_without_frames_does_nothing() -> None:
    f = Feed(trig())
    assert f.wait(10) == []
    assert f.rt.phase is RulePhase.WAITING


# -- validação e configuração ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [
        {"confirm_frames": 0},
        {"release_frames": 51},
        {"delay_ms": -1},
        {"cooldown_ms": -5},
        {"mode": FireMode.CONTINUOUS, "cooldown_ms": 50},
        {"max_executions": -1},
    ],
)
def test_trigger_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TriggerSettings(**kwargs)  # type: ignore[arg-type]


def test_config_roundtrip() -> None:
    from vanthor.rules.models import Rule, RuleStatus

    rule = Rule(
        1, 1, "R", RuleStatus.ACTIVE, 3, ColorCondition((1, 2, 3), tolerance=9),
        TriggerSettings(FireMode.CONTINUOUS, 3, 4, 250, 700, 9), ActionSpec(ActionKind.LOG),
    )
    assert parse_config(rule.config_dict()) == (rule.condition, rule.trigger, rule.action)


@pytest.mark.parametrize(
    "data",
    [[], {"version": 99, "condition": {"color": "#000000"}}, {"version": 1}, {"condition": {"color": "xyz"}}],
)
def test_parse_config_rejects_invalid(data: object) -> None:
    with pytest.raises(ValueError):
        parse_config(data)  # type: ignore[arg-type]
