# Vanthor Bot Click

> Este projeto NÃO é o Bancadia. O `CLAUDE.md` da Área de Trabalho (pasta pai) descreve outro
> projeto (Next.js/pnpm) e não se aplica aqui.

Aplicativo desktop Windows (PT-BR) de automação visual: o usuário define **regras** formadas por
uma *zona observada* + uma *condição de cor* + uma *ação* (clique/tecla em outro ponto). Várias
regras vigiam a tela ao mesmo tempo, e as ações saem em fila (há um único cursor).

## Stack
Python 3.12 (`.venv`), PySide6, NumPy, MSS, SendInput via ctypes, SQLite, PyInstaller + Inno Setup.
Detalhes e justificativas: `docs/ARQUITETURA.md`.
Linux Mint XFCE (X11): mesma UI. `winsys/x11.py` atende entrada, tecla de emergência e monitores; os
caminhos do Windows não mudam. Scripts em `linux/`, detalhes em `docs/LINUX.md`.

## Comandos
- Rodar: `.venv\Scripts\python -m vanthor` (a partir de `src`) ou `.\scripts\run.ps1`
- Testes: `.venv\Scripts\python -m pytest`

## Regras do projeto
- Camadas: `ui` → `core` → (`rules`, `vision`, `capture`, `actions`) → `persistence`/`winsys`.
  O `core` e as camadas abaixo NÃO importam Qt.
- O motor nunca inicia sem clique explícito do usuário; a tecla de emergência precisa ser
  registrada antes de iniciar.
- Nada de dados falsos na interface: toda tela mostra dados reais ou um estado vazio honesto.
- Coordenadas: o Qt usa pixels lógicos; captura e cliques usam pixels físicos. A conversão
  fica num lugar só (`capture/geometry.py`, Fase 2).
- Interface: tema escuro, destaque único `brasa #FA6602`, status sempre com cor + texto/ícone,
  textos curtos em PT-BR, voz ativa.
- Testes obrigatórios para: detecção de cor, coordenadas, motor de regras, execução de cliques.
- Ao fim de cada fase, informe o que mudou, os arquivos, os testes, os problemas e os próximos passos.
