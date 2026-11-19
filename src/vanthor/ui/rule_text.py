"""Frases em português que descrevem uma regra, usadas na lista e no editor."""

from __future__ import annotations

from vanthor.rules.models import FireMode, Rule, TriggerSettings
from vanthor.rules.runtime import RulePhase
from vanthor.vision.color import ColorCondition, ColorSpace, MatchMode

MODE_LABEL: dict[FireMode, str] = {
    FireMode.ON_APPEAR: "Ao aparecer",
    FireMode.CONTINUOUS: "Enquanto estiver visível",
    FireMode.ONCE: "Uma única vez",
}

MODE_HELP: dict[FireMode, str] = {
    FireMode.ON_APPEAR: "Age uma vez cada vez que a cor surge. Só age de novo depois que ela sumir e voltar.",
    FireMode.CONTINUOUS: "Repete a ação enquanto a cor continuar na tela, respeitando o intervalo mínimo.",
    FireMode.ONCE: "Age uma única vez. Para agir de novo, pare e inicie a automação.",
}

PHASE_LABEL: dict[RulePhase, str] = {
    RulePhase.WAITING: "Aguardando a cor",
    RulePhase.CONFIRMING: "Confirmando a cor",
    RulePhase.PRESENT: "Cor na tela",
    RulePhase.SCHEDULED: "Vai agir em instantes",
    RulePhase.COOLDOWN: "No intervalo mínimo",
    RulePhase.DONE: "Concluída nesta execução",
}


def condition_text(c: ColorCondition) -> str:
    space = "RGB" if c.space is ColorSpace.RGB else "HSV"
    tol = f"{c.tolerance}" if c.space is ColorSpace.RGB else f"{c.tolerance}%"
    amount = (
        ("1 pixel" if c.min_pixels == 1 else f"{c.min_pixels} pixels")
        if c.mode is MatchMode.PIXELS
        else f"{c.min_percent:g}% da zona".replace(".", ",")
    )
    return f"{c.hex} em {space}, tolerância {tol}, pelo menos {amount}"


def frames_text(n: int) -> str:
    return "1 quadro" if n == 1 else f"{n} quadros seguidos"


def summary(rule: Rule, zone_name: str | None) -> str:
    where = zone_name or "uma zona que foi excluída"
    t = rule.trigger
    when = {
        FireMode.ON_APPEAR: "quando",
        FireMode.CONTINUOUS: "enquanto",
        FireMode.ONCE: "na primeira vez que",
    }[t.mode]
    wait = f", espera {seconds(t.delay_ms)}" if t.delay_ms else ""
    return f"{when.capitalize()} {where} mostrar {rule.condition.hex}{wait} e então {rule.action.describe()}."


def trigger_details(t: TriggerSettings) -> str:
    parts = [MODE_LABEL[t.mode], f"confirma em {frames_text(t.confirm_frames)}"]
    if t.cooldown_ms:
        parts.append(f"intervalo mínimo {seconds(t.cooldown_ms)}")
    if t.max_executions:
        parts.append(f"no máximo {t.max_executions}×")
    return ", ".join(parts)


def seconds(ms: int) -> str:
    if ms < 1000:
        return f"{ms} ms"
    return f"{ms / 1000:g} s".replace(".", ",")
