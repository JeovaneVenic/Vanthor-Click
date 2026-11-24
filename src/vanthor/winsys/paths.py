"""Locais de arquivos do aplicativo.

Os dados do usuário ficam em %APPDATA%\\VanthorBotClick, para sobreviver a atualizações e
desinstalações que preservam as configurações. A variável VANTHOR_DATA_DIR existe para os
testes e para o modo portátil.
"""

from __future__ import annotations

import os
from pathlib import Path

from vanthor import APP_ID

_RESOURCES = Path(__file__).resolve().parent.parent / "resources"


def data_dir() -> Path:
    override = os.environ.get("VANTHOR_DATA_DIR")
    if override:
        base = Path(override)
    elif os.environ.get("APPDATA"):  # Windows
        base = Path(os.environ["APPDATA"]) / APP_ID
    else:
        # Linux: padrão XDG (~/.local/share/VanthorBotClick), o mesmo lugar dos outros apps do Mint.
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_ID
    base.mkdir(parents=True, exist_ok=True)
    return base


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_dir() / "vanthor.db"


def resource_path(*parts: str) -> Path:
    """Caminho de um recurso empacotado (funciona no código-fonte e no PyInstaller)."""
    return _RESOURCES.joinpath(*parts)
