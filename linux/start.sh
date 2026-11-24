#!/usr/bin/env bash
# Abre o Vanthor Bot Click (instalado pelo install.sh). Repassa argumentos, ex.: --minimized, --self-test.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# O Vanthor clica, captura e registra a tecla de emergência via X11 (padrão do Mint XFCE).
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
# Usado por "Iniciar com o sistema" para gravar o comando certo no autostart do XFCE.
export VANTHOR_LAUNCHER="$HERE/start.sh"

exec "$HERE/venv/bin/python" -m vanthor "$@"
