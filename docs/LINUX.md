# Vanthor Bot Click no Linux Mint XFCE

A interface, as funcionalidades e os dados são os mesmos da versão Windows. Só a camada que conversa
com o sistema muda: no Linux ela usa o **X11** (o padrão do Mint XFCE).

## Requisitos
- Linux Mint **22 ou mais novo**, edição XFCE, 64 bits (x86_64). O Mint 22 traz o Python 3.12, que o
  Vanthor exige. No Mint 21, o Python 3.10 não serve.
- Sessão **X11**. O Vanthor clica, captura a tela e registra a tecla de emergência pelo X11. No
  Wayland, essas três coisas são bloqueadas pelo sistema.
- **Compositor do xfwm4 ligado**: Configurações → Ajustes do gerenciador de janelas → aba Compositor →
  "Habilitar composição de exibição". Ele já vem ligado no Mint. Sem o compositor, a seleção de zonas e o
  conta-gotas aparecem como blocos opacos. O Vanthor avisa ao abrir se ele estiver desligado.

## Instalar
Na pasta do projeto, em um terminal, rode:

```bash
bash linux/install.sh
```

O instalador:
1. confere o sistema;
2. instala, com `sudo`, só as bibliotecas do Qt que faltarem;
3. cria um Python isolado em `~/.local/lib/vanthor-bot-click`, sem mexer no Python do sistema;
4. coloca o Vanthor no menu (Acessórios) e cria o atalho na Área de Trabalho;
5. roda o autoteste.

Para atualizar, rode o mesmo comando de novo. Seus dados são mantidos.

| Arquivo | Para que serve |
|---|---|
| `linux/install.sh` | Instala ou atualiza |
| `linux/start.sh` | Abre o app (é o que o menu e o atalho chamam). Aceita `--minimized` e `--self-test` |
| `linux/uninstall.sh` | Remove o app e pergunta antes de apagar seus dados |
| `requirements.txt` / `pyproject.toml` | Dependências Python. No Linux, entra também o `python-xlib` |

## Abrir
Pelo menu de aplicativos, pelo atalho na Área de Trabalho ou com o comando `vanthor-bot-click`.
O ícone fica na área de notificação do painel do XFCE, como a bandeja do Windows.

## Onde ficam as coisas
| O quê | Onde |
|---|---|
| Programa | `~/.local/lib/vanthor-bot-click/` |
| Zonas, automações, perfis, histórico e logs | `~/.local/share/VanthorBotClick/` |
| "Iniciar com o sistema" | `~/.config/autostart/VanthorBotClick.desktop` |
| Menu e ícone | `~/.local/share/applications/` e `~/.local/share/icons/` |

## Desinstalar

```bash
bash ~/.local/lib/vanthor-bot-click/uninstall.sh
```

## O que mudou no código (e por quê)
Os caminhos do Windows continuam iguais. No Linux, a mesma interface é atendida pelo X11:

| Função | Windows | Linux |
|---|---|---|
| Cliques e teclas | `SendInput` | XTest (`winsys/x11.py`) |
| Tecla de emergência global | `RegisterHotKey` | `XGrabKey` na janela raiz, com variantes de Caps Lock e Num Lock |
| Monitores | `EnumDisplayMonitors` | XRandR. Os nomes batem com os das telas do Qt |
| Dados | `%APPDATA%` | `$XDG_DATA_HOME` (`~/.local/share`) |
| Início com o sistema | Registro `Run` | Arquivo `.desktop` em `~/.config/autostart` |
| Encerrar com o sistema | `WM_ENDSESSION` | `SIGTERM`/`SIGHUP`: para as automações e solta botões e teclas |
| Fontes | Segoe UI / Bahnschrift | Ubuntu, Noto Sans e DejaVu Sans como alternativas |

Os códigos de tecla do Windows são traduzidos para o X11 em `winsys/x11_keymap.py`. Esse arquivo
cobre todas as teclas que o editor de ações aceita.

## Como foi testado
Ambiente: Ubuntu 24.04 (a base do Mint 22) no WSL2, com servidor X11 (Xvfb), xfwm4 com compositor
ligado e área de notificação. Não foi testado numa instalação física do Mint.

- `install.sh`: instalação limpa, reinstalação (atualização) e autoteste passando.
- `uninstall.sh`: remove programa, menu, atalhos e início automático, e mantém os dados se você
  responder "N".
- Suíte automática: 311 testes passaram. Isso inclui os testes com tela, mouse e teclado reais:
  - clique no pixel exato (esquerdo, direito, do meio e duplo);
  - combinações de teclas;
  - regra que vê a cor e clica;
  - tecla de emergência com a janela aberta, pausada e escondida na bandeja;
  - atalho ocupado por outro programa;
  - seletor de ponto e conta-gotas a partir do editor;
  - modo de teste sem clicar;
  - limite de cliques por minuto.
  
  O único teste pulado é o de esconder janelas da captura, que o X11 não permite (veja abaixo).
- App instalado:
  - abre pelo comando do menu;
  - uma segunda instância só avisa e fecha;
  - um `SIGTERM` (logout/desligamento) encerra com código 0 e registra "Aplicativo encerrado" no log;
  - "Iniciar com o sistema" grava o arquivo certo em `~/.config/autostart`.
- Dependências: o ambiente instalado tem só PySide6, NumPy, MSS e python-xlib, nenhum pacote do Windows.

## Limitações conhecidas
- **As janelas do Vanthor podem aparecer na captura.** O X11 não tem o equivalente ao
  `WDA_EXCLUDEFROMCAPTURE` do Windows. O que foi feito:
  - o conta-gotas congela a tela antes de abrir;
  - a borda das zonas é desenhada por fora da zona.
  
  Mesmo assim, não deixe a pílula "Vigiando" nem os destaques do modo de teste sobre uma zona observada.
- **Wayland não é suportado.**
- A tecla de emergência não registra uma combinação que outro programa já usa. O editor avisa quando
  isso acontece, como no Windows.
- As fontes são parecidas, mas não idênticas às do Windows: Segoe UI e Bahnschrift são da Microsoft.
- Na primeira vez em que você abrir o atalho da Área de Trabalho, o XFCE pode perguntar se confia nele.
  O instalador já tenta marcá-lo como confiável, mas isso depende do serviço `gvfs` do desktop.
