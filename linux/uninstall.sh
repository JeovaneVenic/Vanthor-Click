#!/usr/bin/env bash
# Desinstala o Vanthor Bot Click do Linux. Pergunta antes de apagar seus dados.
set -euo pipefail

APP_ID="vanthor-bot-click"
INSTALL_DIR="$HOME/.local/lib/$APP_ID"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/VanthorBotClick"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps/$APP_ID.png"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/VanthorBotClick.desktop"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

# Com o app aberto, uma automação poderia estar clicando: pedimos para encerrar pelo próprio app,
# que para tudo e solta botões e teclas antes de sair.
if pgrep -f "$INSTALL_DIR/venv/bin/python -m vanthor" >/dev/null 2>&1; then
    echo "O Vanthor está aberto. Encerre-o primeiro: ícone da bandeja → Sair."
    read -r -p "Ou digite S para encerrá-lo com segurança agora: " resposta
    if [[ "${resposta,,}" == "s" ]]; then
        pkill -TERM -f "$INSTALL_DIR/venv/bin/python -m vanthor" || true
        for _ in $(seq 1 20); do
            pgrep -f "$INSTALL_DIR/venv/bin/python -m vanthor" >/dev/null 2>&1 || break
            sleep 0.25
        done
    else
        echo "Nada foi removido."
        exit 1
    fi
fi

rm -f "$HOME/.local/bin/$APP_ID" "$APPS_DIR/$APP_ID.desktop" "$DESKTOP_DIR/$APP_ID.desktop" "$ICON" "$AUTOSTART"
rm -rf "$INSTALL_DIR"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS_DIR" || true
echo "Vanthor Bot Click removido (programa, menu, atalhos e início automático)."

if [[ -d "$DATA_DIR" ]]; then
    read -r -p "Apagar também suas zonas, automações, perfis e histórico? [s/N] " resposta
    if [[ "${resposta,,}" == "s" ]]; then
        rm -rf "$DATA_DIR"
        echo "Dados apagados."
    else
        echo "Dados mantidos em $DATA_DIR (recuperados se reinstalar)."
    fi
fi
