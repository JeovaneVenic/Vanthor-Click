#!/usr/bin/env bash
# Instala o Vanthor Bot Click no Linux Mint XFCE (64 bits).
#
#   bash linux/install.sh
#
# O que faz:
#   1. Confere o sistema (x86_64, X11, Python 3.12+).
#   2. Instala, com sudo, SÓ as bibliotecas do sistema que estiverem faltando (Qt precisa delas).
#   3. Cria um ambiente Python isolado em ~/.local/lib/vanthor-bot-click (não mexe no Python do sistema).
#   4. Coloca o Vanthor no menu de aplicativos e cria o atalho na Área de Trabalho.
#   5. Roda o autoteste do Vanthor neste computador.
# Para reinstalar ou atualizar, rode de novo: seus dados (~/.local/share/VanthorBotClick) são mantidos.
set -euo pipefail

APP_ID="vanthor-bot-click"
APP_NAME="Vanthor Bot Click"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="$HOME/.local/lib/$APP_ID"
BIN_LINK="$HOME/.local/bin/$APP_ID"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"

passo() { printf '\n\033[1;38;5;208m==> %s\033[0m\n' "$1"; }
ok()    { printf '    \033[32m✓\033[0m %s\n' "$1"; }
aviso() { printf '    \033[33m!\033[0m %s\n' "$1"; }
falha() { printf '\n\033[31mERRO:\033[0m %s\n' "$1" >&2; exit 1; }

passo "Conferindo o sistema"
[[ "$(uname -m)" == "x86_64" ]] || falha "O Vanthor precisa de um sistema de 64 bits (x86_64)."
if [[ -r /etc/os-release ]]; then
    . /etc/os-release
    ok "Sistema: ${PRETTY_NAME:-desconhecido}"
    [[ "${ID:-}" == "linuxmint" ]] || aviso "Feito para o Linux Mint XFCE; em outra distribuição pode precisar de ajustes."
fi
if [[ "${XDG_SESSION_TYPE:-x11}" == "wayland" ]]; then
    aviso "Sessão Wayland detectada. O Vanthor clica e captura usando X11 (padrão do Mint XFCE)."
fi
command -v python3 >/dev/null || falha "python3 não encontrado."
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    falha "É preciso Python 3.12 ou mais novo (Linux Mint 22 ou superior). Encontrado: $(python3 --version)"
fi
ok "$(python3 --version)"

passo "Bibliotecas do sistema"
# Qt 6 (interface) sobre X11 e o venv do Python. Só instala o que faltar.
PACOTES=(python3-venv libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1
         libxcb-render-util0 libxcb-randr0 libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1
         libegl1 libgl1 libfontconfig1 libdbus-1-3 libx11-6 libxext6 libxtst6)
FALTANDO=()
for pacote in "${PACOTES[@]}"; do
    dpkg -s "$pacote" >/dev/null 2>&1 || FALTANDO+=("$pacote")
done
if ((${#FALTANDO[@]})); then
    aviso "Instalando: ${FALTANDO[*]} (vai pedir sua senha)"
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${FALTANDO[@]}"
fi
ok "Todas presentes"

passo "Instalando o Vanthor em $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/python" -m pip install --quiet --upgrade "$SRC_DIR"
install -m 755 "$SRC_DIR/linux/start.sh" "$INSTALL_DIR/start.sh"
install -m 755 "$SRC_DIR/linux/uninstall.sh" "$INSTALL_DIR/uninstall.sh"
mkdir -p "$(dirname "$BIN_LINK")"
ln -sf "$INSTALL_DIR/start.sh" "$BIN_LINK"
ok "Programa instalado (comando: $APP_ID)"

passo "Atalhos"
mkdir -p "$APPS_DIR" "$ICON_DIR"
install -m 644 "$SRC_DIR/src/vanthor/resources/brand/app-256.png" "$ICON_DIR/$APP_ID.png"
cat > "$APPS_DIR/$APP_ID.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$APP_NAME
GenericName=Automação visual da tela
Comment=Observa áreas da tela e clica ou digita quando uma cor aparece
Exec="$INSTALL_DIR/start.sh"
Icon=$APP_ID
Terminal=false
Categories=Utility;
StartupNotify=true
StartupWMClass=$APP_NAME
DESKTOP
chmod 644 "$APPS_DIR/$APP_ID.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS_DIR" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" || true
ok "Menu de aplicativos → Acessórios → $APP_NAME"

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [[ -d "$DESKTOP_DIR" ]]; then
    install -m 755 "$APPS_DIR/$APP_ID.desktop" "$DESKTOP_DIR/$APP_ID.desktop"
    # XFCE 4.18+: marca o atalho como confiável (sem o aviso "lançador não confiável").
    if command -v gio >/dev/null; then
        gio set "$DESKTOP_DIR/$APP_ID.desktop" metadata::xfce-exe-checksum \
            "$(sha256sum "$DESKTOP_DIR/$APP_ID.desktop" | cut -d' ' -f1)" 2>/dev/null || true
    fi
    ok "Atalho na Área de Trabalho"
fi

passo "Verificando se tudo funciona neste computador"
if "$INSTALL_DIR/start.sh" --self-test >/dev/null 2>&1; then
    ok "Autoteste passou"
else
    aviso "O autoteste encontrou um problema. Relatório: ~/.local/share/VanthorBotClick/self-test.json"
fi

printf '\n\033[1;32mPronto!\033[0m Abra o %s pelo menu de aplicativos, pelo atalho na Área de Trabalho\nou com o comando: %s\n' "$APP_NAME" "$APP_ID"
printf 'Para desinstalar: bash %s\n' "$INSTALL_DIR/uninstall.sh"
