"""Textos de interface compartilhados: um mesmo estado tem um mesmo nome em todo lugar."""

from __future__ import annotations

import sys

from vanthor.core.engine import EngineState
from vanthor.ui.theme.tokens import Color

STATE_LABEL: dict[EngineState, str] = {
    EngineState.STOPPED: "Parado",
    EngineState.RUNNING: "Vigiando",
    EngineState.PAUSED: "Pausado",
}

STATE_COLOR: dict[EngineState, str] = {
    EngineState.STOPPED: Color.TEXT_3,
    EngineState.RUNNING: Color.BRASA,
    EngineState.PAUSED: Color.STEEL,
}


def format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_count(value: int) -> str:
    """Separador de milhar brasileiro, independente do locale do sistema."""
    return f"{value:,}".replace(",", ".")


def plural(count: int, singular: str, plural_form: str) -> str:
    return f"{count} {singular if count == 1 else plural_form}"


# Nome do sistema nos textos da interface: no Windows continua "Windows"; no Linux, "sistema".
SYSTEM = "Windows" if sys.platform == "win32" else "sistema"
