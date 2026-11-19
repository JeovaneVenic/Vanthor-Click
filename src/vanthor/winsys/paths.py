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
    else:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) / APP_ID if appdata else Path.home() / f".{APP_ID.lower()}"
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
