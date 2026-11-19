"""O que uma automação faz: registrar, clicar, pressionar teclas ou uma sequência disso."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from vanthor.winsys.keys import KeyError_, parse_combo

MAX_STEPS: Final = 20
MAX_WAIT_MS: Final = 60_000
MAX_TOTAL_WAIT_MS: Final = 120_000


class ActionKind(StrEnum):
    LOG = "log"  # só registra no histórico
    CLICK = "click"  # um clique num ponto
    KEYS = "keys"  # uma combinação de teclas
    SEQUENCE = "sequence"  # vários passos em ordem (cliques, teclas, esperas)


class MouseButton(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


BUTTON_LABEL: dict[MouseButton, str] = {
    MouseButton.LEFT: "esquerdo",
    MouseButton.RIGHT: "direito",
    MouseButton.MIDDLE: "do meio",
}


@dataclass(frozen=True, slots=True)
class ClickStep:
    x: int  # pixels físicos no desktop virtual
    y: int
    button: MouseButton = MouseButton.LEFT
    double: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"type": "click", "x": self.x, "y": self.y, "button": self.button.value, "double": self.double}

    def describe(self) -> str:
        kind = "Clique duplo" if self.double else "Clique"
        return f"{kind} {BUTTON_LABEL[self.button]} em {self.x}, {self.y}"


@dataclass(frozen=True, slots=True)
class KeyStep:
    combo: str

    def __post_init__(self) -> None:
        try:
            label = parse_combo(self.combo).label
        except KeyError_ as exc:
            raise ValueError(str(exc)) from exc
        object.__setattr__(self, "combo", label)  # forma canônica: "Ctrl+S"

    def to_dict(self) -> dict[str, Any]:
        return {"type": "keys", "combo": self.combo}

    def describe(self) -> str:
        return f"Pressionar {self.combo}"


@dataclass(frozen=True, slots=True)
class WaitStep:
    ms: int

    def __post_init__(self) -> None:
        if not 1 <= self.ms <= MAX_WAIT_MS:
            raise ValueError(f"Espera entre 1 ms e {MAX_WAIT_MS // 1000} s")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "wait", "ms": self.ms}

    def describe(self) -> str:
        return f"Esperar {self.ms} ms" if self.ms < 1000 else f"Esperar {self.ms / 1000:g} s".replace(".", ",")


Step = ClickStep | KeyStep | WaitStep


def step_from_dict(data: dict[str, Any]) -> Step:
    if not isinstance(data, dict):
        raise ValueError("Passo precisa ser um objeto")
    kind = data.get("type")
    try:
        if kind == "click":
            return ClickStep(int(data["x"]), int(data["y"]), MouseButton(data.get("button", "left")),
                             bool(data.get("double", False)))
        if kind == "keys":
            return KeyStep(str(data["combo"]))
        if kind == "wait":
            return WaitStep(int(data["ms"]))
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Passo inválido: {exc}") from exc
    raise ValueError(f"Tipo de passo desconhecido: {kind!r}")


@dataclass(frozen=True, slots=True)
class ActionSpec:
    kind: ActionKind = ActionKind.LOG
    steps: tuple[Step, ...] = field(default_factory=tuple)
    restore_cursor: bool = True  # devolve o mouse para onde estava depois de clicar

    def __post_init__(self) -> None:
        n = len(self.steps)
        if self.kind is ActionKind.LOG and n:
            raise ValueError("Registrar no histórico não tem passos")
        if self.kind is ActionKind.CLICK and (n != 1 or not isinstance(self.steps[0], ClickStep)):
            raise ValueError("Escolha o ponto do clique")
        if self.kind is ActionKind.KEYS and (n != 1 or not isinstance(self.steps[0], KeyStep)):
            raise ValueError("Escolha as teclas")
        if self.kind is ActionKind.SEQUENCE:
            if not any(isinstance(s, ClickStep | KeyStep) for s in self.steps):
                raise ValueError("A sequência precisa de pelo menos um clique ou tecla")
            if n > MAX_STEPS:
                raise ValueError(f"A sequência aceita até {MAX_STEPS} passos")
            if sum(s.ms for s in self.steps if isinstance(s, WaitStep)) > MAX_TOTAL_WAIT_MS:
                raise ValueError("As esperas da sequência somam mais de 2 minutos")

    @property
    def clicks(self) -> int:
        return sum(2 if s.double else 1 for s in self.steps if isinstance(s, ClickStep))

    @property
    def moves_mouse(self) -> bool:
        return any(isinstance(s, ClickStep) for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "steps": [s.to_dict() for s in self.steps],
            "restore_cursor": self.restore_cursor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionSpec:
        if not isinstance(data, dict):
            raise ValueError("Ação precisa ser um objeto")
        try:
            raw_steps = data.get("steps", [])
            if not isinstance(raw_steps, list):
                raise ValueError("Passos precisam ser uma lista")
            return cls(
                kind=ActionKind(data.get("kind", ActionKind.LOG.value)),
                steps=tuple(step_from_dict(s) for s in raw_steps),
                restore_cursor=bool(data.get("restore_cursor", True)),
            )
        except (TypeError, AttributeError) as exc:
            raise ValueError(f"Ação inválida: {exc}") from exc

    def describe(self) -> str:
        if self.kind is ActionKind.LOG:
            return "registrar no histórico"
        if self.kind in (ActionKind.CLICK, ActionKind.KEYS):
            text = self.steps[0].describe()
            return text[0].lower() + text[1:]
        return f"executar uma sequência de {len(self.steps)} passos"
