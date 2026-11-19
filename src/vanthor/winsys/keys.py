"""Nomes de teclas ↔ códigos virtuais do Windows (VK), compartilhado por atalhos e ações."""

from __future__ import annotations

from dataclasses import dataclass

MODIFIER_VK: dict[str, tuple[int, str]] = {
    "ctrl": (0x11, "Ctrl"),
    "control": (0x11, "Ctrl"),
    "alt": (0x12, "Alt"),
    "shift": (0x10, "Shift"),
    "meta": (0x5B, "Win"),
    "win": (0x5B, "Win"),
}

NAMED_KEYS: dict[str, tuple[int, str]] = {
    "esc": (0x1B, "Esc"),
    "escape": (0x1B, "Esc"),
    "pause": (0x13, "Pause"),
    "break": (0x13, "Pause"),
    "space": (0x20, "Espaço"),
    "espaço": (0x20, "Espaço"),
    "tab": (0x09, "Tab"),
    "backspace": (0x08, "Backspace"),
    "return": (0x0D, "Enter"),
    "enter": (0x0D, "Enter"),
    "ins": (0x2D, "Insert"),
    "insert": (0x2D, "Insert"),
    "del": (0x2E, "Delete"),
    "delete": (0x2E, "Delete"),
    "home": (0x24, "Home"),
    "end": (0x23, "End"),
    "pgup": (0x21, "Page Up"),
    "pageup": (0x21, "Page Up"),
    "pgdown": (0x22, "Page Down"),
    "pagedown": (0x22, "Page Down"),
    "left": (0x25, "Esquerda"),
    "esquerda": (0x25, "Esquerda"),
    "up": (0x26, "Cima"),
    "cima": (0x26, "Cima"),
    "right": (0x27, "Direita"),
    "direita": (0x27, "Direita"),
    "down": (0x28, "Baixo"),
    "baixo": (0x28, "Baixo"),
    "scrolllock": (0x91, "Scroll Lock"),
    "print": (0x2C, "Print Screen"),
    "printscreen": (0x2C, "Print Screen"),
}

# Teclas que o Windows exige marcar como "estendidas" no SendInput (senão viram as do teclado numérico).
EXTENDED_VK = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B})


class KeyError_(ValueError):
    """Texto de tecla inválido."""


@dataclass(frozen=True, slots=True)
class KeyCombo:
    modifiers: tuple[int, ...]  # VKs na ordem em que serão pressionados
    vk: int
    label: str

    @property
    def is_bare(self) -> bool:
        return not self.modifiers


def parse_combo(text: str) -> KeyCombo:
    """"Ctrl+Shift+S", "Enter", "F5", "Alt+Tab", "a" → teclas virtuais."""
    parts = [p.strip() for p in text.split("+")]
    if not text.strip() or any(not p for p in parts):
        raise KeyError_("Atalho vazio ou malformado")
    modifiers: list[int] = []
    labels: list[str] = []
    for part in parts[:-1]:
        entry = MODIFIER_VK.get(part.lower())
        if entry is None:
            raise KeyError_(f"Modificador desconhecido: {part}")
        if entry[0] in modifiers:
            raise KeyError_(f"Modificador repetido: {part}")
        modifiers.append(entry[0])
        labels.append(entry[1])
    vk, label = parse_key(parts[-1])
    return KeyCombo(tuple(modifiers), vk, "+".join([*labels, label]))


def parse_key(key: str) -> tuple[int, str]:
    lowered = key.lower()
    if lowered in NAMED_KEYS:
        return NAMED_KEYS[lowered]
    if len(key) == 1 and key.isascii() and key.isalnum():
        return ord(key.upper()), key.upper()
    if lowered.startswith("f") and lowered[1:].isdigit():
        number = int(lowered[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1, f"F{number}"
    raise KeyError_(f"Tecla não suportada: {key}")
