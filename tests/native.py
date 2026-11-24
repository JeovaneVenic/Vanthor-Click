"""Mouse, teclado e atalhos REAIS do sistema para os testes de ponta a ponta.

No Windows faz exatamente o que os testes já faziam (SendInput, keybd_event, RegisterHotKey).
No Linux usa o X11 (XTest e XGrabKey), o mesmo caminho do aplicativo.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

# Sistemas em que os testes com tela, mouse e teclado reais rodam.
REAL_DESKTOP = sys.platform in ("win32", "linux")

MOD_ALT, MOD_CONTROL = 0x0001, 0x0002
_MOD_NOREPEAT = 0x4000


def input_backend_class() -> type:
    """Classe que o aplicativo usa para clicar e teclar neste sistema."""
    if sys.platform == "win32":
        from vanthor.winsys.input import Win32Input

        return Win32Input
    from vanthor.winsys.x11 import X11Input

    return X11Input


def real_input() -> object:
    from vanthor.winsys.input import create_input_backend

    return create_input_backend()


def press_key(vk: int) -> None:
    """Pressiona e solta uma tecla de verdade, como alguém no teclado."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
        return
    backend = real_input()
    backend.key(vk, True)  # type: ignore[attr-defined]
    backend.key(vk, False)  # type: ignore[attr-defined]


@contextmanager
def held_by_another_program(modifiers: int, vk: int) -> Iterator[None]:
    """Simula outro programa ocupando o atalho global (modificadores no formato do RegisterHotKey)."""
    if sys.platform == "win32":
        import ctypes

        user32 = ctypes.windll.user32
        assert user32.RegisterHotKey(None, 0x7A7B, modifiers | _MOD_NOREPEAT, vk), "atalho já ocupado nesta máquina"
        try:
            yield
        finally:
            user32.UnregisterHotKey(None, 0x7A7B)
        return
    from vanthor.winsys.x11 import _grab, _keycode, _open, _ungrab, _x_modifiers

    d = _open()  # uma conexão X11 própria = outro cliente
    keycode, mask = _keycode(d, vk), _x_modifiers(modifiers)
    try:
        assert _grab(d, keycode, mask), "atalho já ocupado nesta máquina"
        try:
            yield
        finally:
            _ungrab(d, keycode, mask)
    finally:
        d.close()
