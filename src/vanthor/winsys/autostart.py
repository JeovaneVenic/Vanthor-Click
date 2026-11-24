"""Iniciar com o sistema.

- Windows: HKCU\\...\\Run (por usuário, sem pedir permissão de administrador).
- Linux (XFCE e outros): arquivo .desktop em ~/.config/autostart, o padrão XDG que o XFCE lê em
  "Sessão e inicialização".
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from vanthor import APP_ID

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    if sys.platform.startswith("linux"):
        # Instalado pelo linux/install.sh: o start.sh informa o próprio caminho (prepara o ambiente).
        launcher = os.environ.get("VANTHOR_LAUNCHER")
        if launcher:
            return f'"{launcher}" --minimized'
        return f'"{sys.executable}" -m vanthor --minimized'
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    # Executando do código-fonte: usa pythonw para não abrir console no login.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpreter}" -m vanthor --minimized'


def _xdg_autostart_file() -> Path:
    config = os.environ.get("XDG_CONFIG_HOME")
    return (Path(config) if config else Path.home() / ".config") / "autostart" / f"{APP_ID}.desktop"


def xdg_autostart_entry(command: str) -> str:
    """Conteúdo do .desktop de inicialização automática (função pura, testável)."""
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Vanthor Bot Click",
        "Comment=Automação visual da tela (abre em segundo plano)",
        f"Exec={command}",
        "Icon=vanthor-bot-click",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        "Hidden=false",
    ]
    return "\n".join(lines) + "\n"


def is_enabled() -> bool:
    if sys.platform.startswith("linux"):
        return _xdg_autostart_file().exists()
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_ID)
            return True
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool) -> None:
    if sys.platform.startswith("linux"):
        path = _xdg_autostart_file()
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(xdg_autostart_entry(launch_command()), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
        return
    if sys.platform != "win32":
        raise OSError("Inicialização automática não é suportada neste sistema")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_ID)
            except FileNotFoundError:
                pass
