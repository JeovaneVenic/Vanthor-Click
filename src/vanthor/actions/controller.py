"""Cliques e teclas com rastreamento do que está pressionado, para nunca deixar nada "preso".

Se uma sequência for interrompida (emergência, erro) entre o "apertar" e o "soltar", o
``release_all`` solta tudo que ficou pressionado. É chamado em toda parada do motor.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

from vanthor.actions.models import ClickStep, MouseButton
from vanthor.winsys.keys import KeyCombo

log = logging.getLogger(__name__)

# Pausas curtas entre eventos: rápido o bastante para parecer instantâneo e lento o bastante
# para programas que leem o estado do mouse a cada quadro registrarem o clique.
_PRESS_S = 0.02
_DOUBLE_GAP_S = 0.04
_MODIFIER_S = 0.03


class InputBackend(Protocol):
    def get_cursor(self) -> tuple[int, int]: ...
    def set_cursor(self, x: int, y: int) -> None: ...
    def mouse(self, button: MouseButton, down: bool) -> None: ...
    def key(self, vk: int, down: bool) -> None: ...


class InputController:
    def __init__(self, backend: InputBackend, sleep: Callable[[float], None] = time.sleep) -> None:
        self._backend = backend
        self._sleep = sleep
        self._lock = threading.RLock()
        self._held_buttons: set[MouseButton] = set()
        self._held_keys: list[int] = []

    def cursor(self) -> tuple[int, int]:
        return self._backend.get_cursor()

    def move(self, x: int, y: int) -> None:
        self._backend.set_cursor(x, y)

    def click(self, step: ClickStep) -> None:
        self._backend.set_cursor(step.x, step.y)
        for index in range(2 if step.double else 1):
            if index:
                self._sleep(_DOUBLE_GAP_S)
            self._button(step.button, down=True)
            self._sleep(_PRESS_S)
            self._button(step.button, down=False)

    def press(self, combo: KeyCombo) -> None:
        for vk in combo.modifiers:
            self._key(vk, down=True)
            # Pausa curta, como a de uma pessoa digitando: programas que leem o teclado a cada
            # quadro (jogos, apps de captura) podem não perceber modificadores instantâneos.
            self._sleep(_MODIFIER_S)
        self._key(combo.vk, down=True)
        self._sleep(_PRESS_S)
        self._key(combo.vk, down=False)
        for vk in reversed(combo.modifiers):
            self._key(vk, down=False)

    @property
    def anything_held(self) -> bool:
        with self._lock:
            return bool(self._held_buttons or self._held_keys)

    def release_all(self) -> None:
        """Solta tudo que esta automação pressionou. Seguro para chamar de qualquer thread."""
        with self._lock:
            buttons, keys = list(self._held_buttons), list(reversed(self._held_keys))
            self._held_buttons.clear()
            self._held_keys.clear()
        for button in buttons:
            try:
                self._backend.mouse(button, down=False)
            except Exception:
                log.exception("Falha ao soltar o botão %s", button)
        for vk in keys:
            try:
                self._backend.key(vk, down=False)
            except Exception:
                log.exception("Falha ao soltar a tecla %#x", vk)
        if buttons or keys:
            log.warning("Entradas liberadas após interrupção: botões=%s teclas=%s", buttons, keys)

    def _button(self, button: MouseButton, down: bool) -> None:
        with self._lock:
            if down:
                self._held_buttons.add(button)  # marca ANTES: se o envio falhar no meio, ainda soltamos
            self._backend.mouse(button, down)
            if not down:
                self._held_buttons.discard(button)

    def _key(self, vk: int, down: bool) -> None:
        with self._lock:
            if down:
                self._held_keys.append(vk)
            self._backend.key(vk, down)
            if not down and vk in self._held_keys:
                self._held_keys.remove(vk)
