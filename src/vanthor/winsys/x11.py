"""Equivalentes Linux (X11) das APIs do Windows usadas pelo Vanthor.

Alvo: Linux Mint XFCE, que roda sobre X11 (Xorg) com o gerenciador de janelas xfwm4.

| Windows (winsys/*)            | Aqui (X11)                                   |
|-------------------------------|----------------------------------------------|
| EnumDisplayMonitors           | XRandR (saídas ativas e suas posições)       |
| SetCursorPos / SendInput      | XTest (mesmos eventos que um mouse/teclado)  |
| RegisterHotKey + WM_HOTKEY    | XGrabKey na janela raiz + thread de eventos  |

Só é importado no Linux (os módulos do Windows decidem por ``sys.platform``). Usa python-xlib,
uma biblioteca Python pura, sem compilação.
"""

from __future__ import annotations

import logging
import select
import threading
from collections.abc import Callable

from Xlib import X, XK, display, error
from Xlib.ext import randr, xtest

from vanthor.actions.models import MouseButton
from vanthor.capture.geometry import ScreenRect
from vanthor.winsys.x11_keymap import vk_to_keysym_name

log = logging.getLogger(__name__)

# Modificadores do RegisterHotKey (winsys/hotkeys.py) → máscaras do X11.
_MOD_ALT, _MOD_CONTROL, _MOD_SHIFT, _MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
_X_MODS = {_MOD_ALT: X.Mod1Mask, _MOD_CONTROL: X.ControlMask, _MOD_SHIFT: X.ShiftMask, _MOD_WIN: X.Mod4Mask}
# Caps Lock e Num Lock mudam o estado dos modificadores: o atalho precisa funcionar com eles ligados.
_LOCK_VARIANTS = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
_BUTTONS = {MouseButton.LEFT: 1, MouseButton.MIDDLE: 2, MouseButton.RIGHT: 3}


class X11Error(RuntimeError):
    """O servidor X recusou ou não está disponível."""


def _open() -> display.Display:
    try:
        return display.Display()
    except Exception as exc:  # DISPLAY ausente, sem permissão, servidor caído
        raise X11Error(f"Não foi possível conectar ao servidor gráfico (X11): {exc}") from exc


def _x_modifiers(windows_mods: int) -> int:
    mask = 0
    for win_mod, x_mod in _X_MODS.items():
        if windows_mods & win_mod:
            mask |= x_mod
    return mask


def _keycode(d: display.Display, vk: int) -> int:
    keysym = XK.string_to_keysym(vk_to_keysym_name(vk))
    keycode = d.keysym_to_keycode(keysym) if keysym else 0
    if not keycode:
        raise X11Error(f"A tecla {vk_to_keysym_name(vk)} não existe no layout de teclado atual")
    return int(keycode)


# -- monitores ----------------------------------------------------------------------------------
def enumerate_monitors_x11() -> list[tuple[str, ScreenRect, bool]]:
    """(nome da saída, retângulo em pixels reais, é a principal), como o Xrandr/xfce4-display vê.

    Os nomes ("eDP-1", "HDMI-1") são os mesmos que o Qt usa em ``QScreen.name()`` no X11.
    """
    d = _open()
    try:
        root = d.screen().root
        if not d.has_extension("RANDR"):
            geo = root.get_geometry()
            return [("", ScreenRect(0, 0, geo.width, geo.height), True)]
        resources = randr.get_screen_resources(root)
        primary = randr.get_output_primary(root).output
        found: list[tuple[str, ScreenRect, bool]] = []
        for output in resources.outputs:
            info = randr.get_output_info(d, output, resources.config_timestamp)
            if not info.crtc:  # saída desligada ou sem monitor
                continue
            crtc = randr.get_crtc_info(d, info.crtc, resources.config_timestamp)
            if crtc.width <= 0 or crtc.height <= 0:
                continue
            name = info.name.decode() if isinstance(info.name, bytes) else str(info.name)
            found.append((name, ScreenRect(crtc.x, crtc.y, crtc.width, crtc.height), output == primary))
        if not found:
            # Servidor sem monitor físico (acesso remoto, driver "dummy", VNC): o XRandR não lista
            # nenhuma saída ativa, mas a tela existe. Sem este recurso, nenhuma zona seria capturada
            # ("fora das telas") e nenhum clique sairia.
            geo = root.get_geometry()
            return [("", ScreenRect(0, 0, geo.width, geo.height), True)]
        if not any(p for _, _, p in found):
            name, rect, _ = found[0]  # sem principal definida: a primeira saída faz esse papel
            found[0] = (name, rect, True)
        return found
    finally:
        d.close()


# -- mouse e teclado ----------------------------------------------------------------------------
class X11Input:
    """Mesma interface do ``Win32Input``: o ``InputController`` não sabe em qual sistema está."""

    def __init__(self) -> None:
        self._d = _open()
        if not self._d.has_extension("XTEST"):
            self._d.close()
            raise X11Error("A extensão XTEST do X11 não está disponível: não é possível clicar")
        self._root = self._d.screen().root
        self._lock = threading.Lock()  # python-xlib: uma conexão, um uso por vez

    def get_cursor(self) -> tuple[int, int]:
        with self._lock:
            pointer = self._root.query_pointer()
        return int(pointer.root_x), int(pointer.root_y)

    def set_cursor(self, x: int, y: int) -> None:
        with self._lock:
            xtest.fake_input(self._d, X.MotionNotify, x=x, y=y)
            self._d.sync()

    def mouse(self, button: MouseButton, down: bool) -> None:
        with self._lock:
            xtest.fake_input(self._d, X.ButtonPress if down else X.ButtonRelease, _BUTTONS[button])
            self._d.sync()

    def key(self, vk: int, down: bool) -> None:
        with self._lock:
            keycode = _keycode(self._d, vk)
            xtest.fake_input(self._d, X.KeyPress if down else X.KeyRelease, keycode)
            self._d.sync()


# -- atalho global (tecla de emergência) ---------------------------------------------------------
class _GrabCatcher:
    """Captura o erro assíncrono do XGrabKey (BadAccess = outro programa já usa o atalho)."""

    def __init__(self) -> None:
        self.failed = False

    def __call__(self, err: error.XError, *_args: object) -> None:
        self.failed = True


def _grab(d: display.Display, keycode: int, mask: int) -> bool:
    root = d.screen().root
    catcher = _GrabCatcher()
    for extra in _LOCK_VARIANTS:
        root.grab_key(keycode, mask | extra, True, X.GrabModeAsync, X.GrabModeAsync, onerror=catcher)
    d.sync()
    if catcher.failed:
        _ungrab(d, keycode, mask)
    return not catcher.failed


def _ungrab(d: display.Display, keycode: int, mask: int) -> None:
    root = d.screen().root
    for extra in _LOCK_VARIANTS:
        root.ungrab_key(keycode, mask | extra)
    d.sync()


class X11GlobalHotkey:
    """Mesma interface do ``GlobalHotkey`` do Windows; avisa por ``on_trigger`` (outra thread).

    Como no Windows, o atalho só fica registrado enquanto há automação ativa: um Esc global
    capturado rouba o Esc dos outros programas.
    """

    def __init__(self, on_trigger: Callable[[], None], hotkey_id: int = 0xB0C1) -> None:
        self._on_trigger = on_trigger
        self._id = hotkey_id
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def hotkey_id(self) -> int:
        return self._id

    @property
    def registered(self) -> bool:
        return self._thread is not None

    def register(self, _window: int, hotkey: object) -> bool:
        """``hotkey``: ``winsys.hotkeys.Hotkey`` (modificadores do Windows + tecla virtual)."""
        self.unregister()
        modifiers, vk = getattr(hotkey, "modifiers"), getattr(hotkey, "vk")
        try:
            d = _open()
        except X11Error:
            log.exception("Sem X11 para registrar a tecla de emergência")
            return False
        try:
            keycode, mask = _keycode(d, vk), _x_modifiers(modifiers)
        except X11Error:
            d.close()
            log.exception("Tecla de emergência sem equivalente no teclado atual")
            return False
        if not _grab(d, keycode, mask):
            d.close()
            return False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._listen, args=(d, keycode, mask, self._stop), name="vanthor-x11-hotkey", daemon=True
        )
        self._thread.start()
        return True

    def unregister(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        if thread is not threading.current_thread():
            thread.join(2.0)
        self._thread = None

    def _listen(self, d: display.Display, keycode: int, mask: int, stop: threading.Event) -> None:
        # A própria thread desfaz o grab e fecha a conexão: uma conexão X11 não é usada por duas
        # threads ao mesmo tempo.
        try:
            while not stop.is_set():
                readable, _, _ = select.select([d.fileno()], [], [], 0.1)
                if not readable and not d.pending_events():
                    continue
                while d.pending_events():
                    event = d.next_event()
                    if event.type == X.KeyPress and event.detail == keycode:
                        try:
                            self._on_trigger()
                        except Exception:
                            log.exception("Falha ao acionar a parada de emergência")
        finally:
            try:
                _ungrab(d, keycode, mask)
            finally:
                d.close()


def combo_taken(modifiers_vks: tuple[int, ...], vk: int) -> bool:
    """Outro programa já capturou esta combinação como atalho global? (equivalente X11)."""
    vk_to_mod = {0x11: _MOD_CONTROL, 0x12: _MOD_ALT, 0x10: _MOD_SHIFT, 0x5B: _MOD_WIN}
    windows_mods = 0
    for m in modifiers_vks:
        windows_mods |= vk_to_mod.get(m, 0)
    try:
        d = _open()
    except X11Error:
        return False
    try:
        keycode, mask = _keycode(d, vk), _x_modifiers(windows_mods)
        if _grab(d, keycode, mask):
            _ungrab(d, keycode, mask)
            return False
        return True
    except X11Error:
        return False
    finally:
        d.close()


def compositor_running() -> bool:
    """O compositor do xfwm4 está ligado? Sem ele não há transparência nas sobreposições."""
    try:
        d = _open()
    except X11Error:
        return False
    try:
        atom = d.intern_atom(f"_NET_WM_CM_S{d.get_default_screen()}")
        return d.get_selection_owner(atom) != X.NONE
    finally:
        d.close()
