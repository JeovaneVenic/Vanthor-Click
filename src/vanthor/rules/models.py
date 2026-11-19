"""Regra (automação): zona + condição de cor + quando disparar + o que fazer."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Final

from vanthor.actions.models import ActionKind, ActionSpec
from vanthor.vision.color import ColorCondition

__all__ = ["ActionKind", "ActionSpec", "FireMode", "Rule", "RuleStatus", "TriggerSettings", "parse_config"]

CONFIG_VERSION: Final = 1


class RuleStatus(StrEnum):
    ACTIVE = "active"  # vigiando quando a automação estiver rodando
    PAUSED = "paused"  # desligada pelo usuário
    DISABLED = "disabled"  # bloqueada por problema (ex.: zona excluída)


class FireMode(StrEnum):
    ON_APPEAR = "on_appear"  # uma vez a cada aparição; só rearma quando a cor some
    CONTINUOUS = "continuous"  # repete enquanto a cor estiver visível, respeitando o intervalo mínimo
    ONCE = "once"  # uma única vez por execução da automação


MIN_CONTINUOUS_COOLDOWN_MS: Final = 100  # repetição contínua sem intervalo viraria rajada de cliques


@dataclass(frozen=True, slots=True)
class TriggerSettings:
    mode: FireMode = FireMode.ON_APPEAR
    confirm_frames: int = 2  # quadros seguidos com a cor para confirmar (evita falso positivo)
    release_frames: int = 2  # quadros seguidos sem a cor para considerar que sumiu (debounce)
    delay_ms: int = 0  # espera entre confirmar e agir; cancela se a cor sumir antes
    cooldown_ms: int = 1000  # intervalo mínimo entre execuções
    max_executions: int = 0  # 0 = sem limite

    def __post_init__(self) -> None:
        if not 1 <= self.confirm_frames <= 50:
            raise ValueError("Quadros para confirmar: de 1 a 50")
        if not 1 <= self.release_frames <= 50:
            raise ValueError("Quadros para considerar que sumiu: de 1 a 50")
        if not 0 <= self.delay_ms <= 600_000:
            raise ValueError("Espera antes de agir: de 0 a 10 minutos")
        if not 0 <= self.cooldown_ms <= 86_400_000:
            raise ValueError("Intervalo mínimo: de 0 a 24 horas")
        if self.mode is FireMode.CONTINUOUS and self.cooldown_ms < MIN_CONTINUOUS_COOLDOWN_MS:
            raise ValueError(f"No modo contínuo, o intervalo mínimo é {MIN_CONTINUOUS_COOLDOWN_MS} ms")
        if not 0 <= self.max_executions <= 1_000_000:
            raise ValueError("Limite de execuções inválido")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "confirm_frames": self.confirm_frames,
            "release_frames": self.release_frames,
            "delay_ms": self.delay_ms,
            "cooldown_ms": self.cooldown_ms,
            "max_executions": self.max_executions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TriggerSettings:
        try:
            return cls(
                mode=FireMode(data.get("mode", FireMode.ON_APPEAR.value)),
                confirm_frames=int(data.get("confirm_frames", 2)),
                release_frames=int(data.get("release_frames", 2)),
                delay_ms=int(data.get("delay_ms", 0)),
                cooldown_ms=int(data.get("cooldown_ms", 1000)),
                max_executions=int(data.get("max_executions", 0)),
            )
        except (TypeError, AttributeError) as exc:
            raise ValueError(f"Configuração de disparo inválida: {exc}") from exc


@dataclass(frozen=True, slots=True)
class Rule:
    id: int
    profile_id: int
    name: str
    status: RuleStatus
    zone_id: int | None
    condition: ColorCondition
    trigger: TriggerSettings = field(default_factory=TriggerSettings)
    action: ActionSpec = field(default_factory=ActionSpec)
    position: int = 0
    detections_total: int = 0
    executions_total: int = 0

    @property
    def runnable(self) -> bool:
        return self.status is RuleStatus.ACTIVE and self.zone_id is not None

    def with_changes(self, **changes: object) -> Rule:
        return replace(self, **changes)  # type: ignore[arg-type]

    def config_dict(self) -> dict[str, Any]:
        return {
            "version": CONFIG_VERSION,
            "condition": self.condition.to_dict(),
            "trigger": self.trigger.to_dict(),
            "action": self.action.to_dict(),
        }

    def behaviour_key(self) -> tuple[object, ...]:
        """O que, se mudar, exige recomeçar a contagem da regra durante a automação."""
        return (self.zone_id, self.condition, self.trigger, self.action)


def parse_config(data: dict[str, Any]) -> tuple[ColorCondition, TriggerSettings, ActionSpec]:
    if not isinstance(data, dict):
        raise ValueError("Configuração da regra precisa ser um objeto")
    version = data.get("version", CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ValueError(f"Versão de configuração não suportada: {version}")
    condition = data.get("condition")
    if not isinstance(condition, dict):
        raise ValueError("Regra sem condição de cor")
    return (
        ColorCondition.from_dict(condition),
        TriggerSettings.from_dict(data.get("trigger") or {}),
        ActionSpec.from_dict(data.get("action") or {}),
    )
