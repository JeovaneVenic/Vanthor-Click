# Vanthor Bot Click: arquitetura

## 1. Ambiente analisado (18/11/2026)

| Item | Encontrado | Decisão |
|---|---|---|
| Python | 3.12 e 3.14 | **3.12** no projeto: suporte maduro em PySide6 e PyInstaller |
| Node | 24 | Usado só para baixar os ícones Lucide |
| Tela | 1920×1080 físico, escala 125% (1536×864 lógico) | Ótimo caso de teste para DPI |
| Inno Setup | Não instalado | Necessário só na Fase 8 |
| Repositório | Só a logo; sem código e sem git | Projeto criado do zero |

## 2. Tecnologias

| Camada | Escolha | Por quê | Alternativas descartadas |
|---|---|---|---|
| Interface | **PySide6 (Qt 6)** | Nativo, leve, sem navegador, pintura customizada | Electron/Tauri: dois runtimes e a visão computacional fica em outro processo |
| Captura | **MSS** (GDI BitBlt por região) | Captura só o retângulo pedido, multi-monitor, coordenadas físicas | DXcam: mais rápido, mas captura o monitor inteiro e falha em algumas GPUs híbridas |
| Visão | **NumPy** | Distância RGB/HSV vetorizada em microssegundos para regiões pequenas | OpenCV: +60 MB no instalador. Volta quando chegar o reconhecimento de imagem |
| Mouse/teclado | **`SendInput` via ctypes** | Coordenadas exatas com DPI, botões liberáveis em emergência, sem o "fail-safe" de canto do PyAutoGUI | PyAutoGUI: lento, impreciso com escala e sem controle fino de liberação |
| Atalho global | **`RegisterHotKey`** | API oficial, sem gancho de teclado (ganchos parecem keylogger para antivírus) | `WH_KEYBOARD_LL`: dispara alerta de antivírus |
| Persistência | **SQLite** (WAL) em `%APPDATA%\VanthorBotClick` | Arquivo único, transacional, sem servidor | JSON solto: sem integridade e sem histórico |
| Empacotamento | **PyInstaller (onedir) + Inno Setup** | O onedir abre rápido e gera menos falso positivo que o onefile | Nuitka: compila mais lento. Pode ser reavaliado na Fase 8 |
| Testes | **pytest + pytest-qt** | Núcleo testável sem interface | |

## 3. Camadas (dependências só apontam para baixo)

```
ui/            Interface Qt: páginas, widgets, tema. Só conversa com o núcleo via AppContext
  bridge.py    Converte eventos do barramento (qualquer thread) em sinais Qt (thread principal)
core/          Motor de automação (thread própria), barramento de eventos. Sem Qt
rules/         Modelos de regra e avaliação: debounce, cooldown, frames, disparo  (Fase 4)
vision/        Detectores plugáveis: cor RGB/HSV/HEX; depois imagem e OCR      (Fase 3)
capture/       Captura por região, mapa de monitores e DPI                     (Fase 2)
actions/       Executores: cliques, sequências, teclas, dry run                (Fase 5)
persistence/   SQLite, migrações, repositórios                                  
winsys/        Windows: atalhos globais, autostart, barra de título, DPI
```

**Fluxo de uma regra (a partir da Fase 4):**
`Motor (tick) → Captura da região → Detector → Avaliador (frames/debounce/cooldown) → Executor (real ou simulado) → Evento → Log + Interface`

**Threads:**
- Thread principal: só a interface.
- Thread do motor: captura, detecção e ação. A interface nunca espera por ela.
- Eventos atravessam via `EventBus` e depois sinal Qt *queued*. A interface nunca toca no motor diretamente, só via `start/pause/stop/snapshot`, que são thread-safe.

## 4. Segurança (não negociável)
- O motor **nunca inicia sozinho**: só `AutomationEngine.start()`, chamado por um clique do usuário.
- A **tecla de emergência** é registrada antes de iniciar. Se o Windows recusar o atalho, a automação **não inicia**.
- Ao parar (por qualquer motivo), os *safety hooks* liberam todos os botões e teclas pressionados.
- Indicador permanente de execução: faixa laranja no topo, título da janela e status na barra lateral.
- Sem burla de anti-cheat, injeção em processos ou ocultação.

## 5. Dificuldades técnicas previstas
1. **DPI misto entre monitores:** o Qt trabalha em pixels lógicos; MSS e `SendInput` trabalham em físicos. Uma camada única de conversão (`capture/geometry.py`) com testes cobre isso.
2. **Sobreposições que não interferem:** janelas *layered* com `WS_EX_TRANSPARENT` e `WDA_EXCLUDEFROMCAPTURE`, para que a captura não enxergue a própria sobreposição.
3. **Esc global "rouba" o Esc de outros apps:** por isso o atalho só fica registrado enquanto a automação está ativa.
4. **Antivírus e SmartScreen:** autoclickers empacotados com PyInstaller costumam gerar falso positivo. Para publicar em sites de download é **fortemente recomendado um certificado de assinatura de código** (OV/EV). Sem ele, o Windows mostra "Editor desconhecido".
5. **Jogos em tela cheia exclusiva:** a captura GDI pode vir preta. É uma limitação documentada, não algo a contornar.

## 6. Direção visual: "o olhar da pantera"
O produto vigia a tela como os olhos da pantera da logo. O único elemento ousado é o **Olho de status**: apagado (aço) quando parado e aceso em brasa, respirando devagar, quando está vigiando. Todo o resto é quieto.

| Token | Valor | Uso |
|---|---|---|
| `bg` | `#101214` | Fundo da janela |
| `surface` / `raised` | `#17191C` / `#1F2226` | Painéis e elementos elevados |
| `line` | `#2A2E33` | Divisórias (só onde separam informação) |
| `text` / `text-2` / `text-3` | `#E4E7EB` / `#9AA1AB` / `#7A818B` | Texto primário, secundário e de apoio (AA no `surface`) |
| `brasa` | `#FA6602` | Destaque único: botão principal e estado "vigiando" |
| `detect` | `#34D399` | Detecção confirmada |
| `sim` | `#22D3EE` | Modo de teste (Dry Run) |
| `danger` | `#F43F5E` | Erro e emergência |

- **Tipografia:** Segoe UI Variable para a interface (nativa e nítida em qualquer DPI) e Bahnschrift para números e marca (a mesma família DIN do "CLICK" da logo).
- **Status** sempre com cor + ícone ou texto (daltonismo).
- **Movimento** só para estado: o olho respira enquanto vigia. Segue a preferência "mostrar animações" do Windows.

## 6.1 Detecção de cor (Fase 3)
| Modo | Tolerância | Regra |
|---|---|---|
| **RGB exato** | 0 a 255 (padrão 15) | Cada canal pode variar até *t* níveis, inclusive. Ex.: #22C55E com 15 aceita R 19–49, G 182–212, B 79–109 |
| **HSV tolerante à luz** | 0 a 100 (padrão 12) | Tom até *t*×1,8° (circular: 359° fica perto de 1°); saturação e brilho até *t*%. Alvos quase cinza (saturação < 10%) ignoram o tom |

- **Critério:** "pelo menos N pixels" (N = 1 equivale a "aparecer em um pixel") ou "pelo menos X% da zona".
- **Onde fica a condição:** pertence à automação (Fase 4), não à zona, para que a mesma zona possa disparar ações diferentes conforme a cor. O "Testar cor" guarda o último ajuste por zona como ponto de partida.
- **Desempenho medido:** zona de 300×50 em 0,4 ms (RGB) e 1,6 ms (HSV). Zona de 800×600 com ruído (pior caso) em 14 ms e 50 ms.

## 6.2 Motor de regras (Fase 4)
Cada regra tem um avaliador independente (`rules/runtime.py`) que transforma quadros em sinais:

```
WAITING ─(N quadros seguidos com a cor)→ PRESENT ─→ SCHEDULED (espera) / COOLDOWN (intervalo) ─(hora)→ FIRE
PRESENT ─(M quadros seguidos sem a cor)→ WAITING   (rearma "ao aparecer"; cancela o que estava agendado)
```

| Parâmetro | Padrão | Por quê |
|---|---|---|
| Confirmar após N quadros | 2 | Um piscar de tela não dispara nada |
| Considerar que sumiu após M quadros | 2 | Debounce: uma falha de um quadro não rearma |
| Esperar antes de agir | 0 ms | Cancelada se a cor sumir antes |
| Intervalo mínimo | 1 s | Nunca age duas vezes em menos tempo; no modo contínuo, mínimo de 100 ms |
| Limite de execuções | sem limite | Para de agir ao atingir o limite (até reiniciar a automação) |

- **Modos:** "Ao aparecer" (padrão, só na transição), "Enquanto estiver visível" (repete) e "Uma única vez".
- **Ciclo do motor:** primeiro a captura (`ZoneMonitor`), depois as regras (`RuleEngine`). Zonas com o mesmo retângulo são capturadas uma vez, e regras com a mesma zona e a mesma condição são analisadas uma vez.
- **Plano de vigia:** só são capturadas zonas ligadas E usadas por regras ativas.
- **Threads:** a captura é fechada dentro da própria thread do motor (o Windows exige isso para liberar o contexto gráfico).

## 6.3 Execução de ações (Fase 5)
```
RuleEngine (thread do motor) ──dispatch──► ActionDispatcher (fila, thread "vanthor-actions")
                                              └─► InputController ──► Win32Input (SetCursorPos + SendInput)
```
- **Ações:** clicar (esquerdo, direito, meio, duplo), pressionar teclas (combinações), sequência (até 20 passos, com esperas) e só registrar.
- **Fila única:** um cursor só, então as ações saem em ordem e o motor nunca trava esperando uma sequência.
- **Uma pendente por regra:** disparos repetidos não acumulam cliques atrasados. Ao pausar, a fila é esvaziada.
- **Parada ou emergência:** o `halt` (safety hook) esvazia a fila, interrompe a sequência entre passos e chama `release_all`, que solta todo botão e tecla que o Vanthor pressionou.
- **Mouse devolvido:** depois de clicar, o cursor volta para onde estava (opção ligada por padrão).
- **Pontos validados na hora de agir:** um ponto fora das telas atuais é recusado, com aviso no histórico.

## 6.4 Segurança e monitoramento (Fase 6)
| Recurso | Como funciona |
|---|---|
| **Modo de teste** | `session.start(dry_run=True)`. A fila percorre os passos sem nunca criar o controlador de entrada; cada clique vira um alvo ciano na tela (`ClickFlashes`) e um evento "teria executado". Os contadores reais não são gravados |
| **Limite por minuto** | `ClickRateLimiter`, uma janela deslizante de 60 s para todas as regras juntas. Recusa a ação inteira (nunca "meio clique") e avisa no máximo a cada 10 s. Vale também no modo de teste |
| **Indicador permanente** | Pílula sempre no topo (`RunIndicator`) com o estado e o botão Parar. Não rouba foco, pode ser arrastada e é invisível para a captura |
| **Bandeja** | Ícone com menu (abrir, iniciar/parar, sair) e notificações de emergência e erros quando o app não está em primeiro plano |
| **Monitoramento** | Estado ao vivo por regra (fase, % da cor, detecções e execuções da sessão), status de captura por zona e histórico filtrável |
| **Sobreposição** | "Mostrar na tela" desenha as zonas e os pontos de clique das regras ativas |
| **Já existentes** | Emergência global, debounce, cooldown, confirmação por quadros, liberação de entradas e registro de tudo no histórico |

## 6.5 Perfis e arquivos de perfil (Fase 7)
- **O perfil guarda zonas e automações.** As configurações gerais (tecla de emergência, limite de cliques, indicador, notificações) são do aplicativo: são travas de segurança e não mudam ao trocar de perfil.
- **Troca de perfil só com a automação parada** (`AutomationSession.switch_profile`).
- **Excluir perfil:** não é possível excluir o perfil em uso nem o último. Há "Desfazer" por 5 s, e as zonas e automações saem em cascata.

**Formato `*.vanthor.json` (versão 1):**
```json
{
  "format": "vanthor-bot-click/profile", "version": 1, "app_version": "0.1.0", "exported_at": "…",
  "profile": {"name": "Trabalho"},
  "zones": [{"ref": "z1", "name": "…", "color": "#34D399", "rect": {"x": 0, "y": 0, "width": 10, "height": 10},
             "monitor": "\\\\.\\DISPLAY1", "interval_ms": 100, "enabled": true, "color_draft": null}],
  "rules": [{"name": "…", "status": "active", "zone": "z1",
             "condition": {…}, "trigger": {…}, "action": {…}}]
}
```
- As automações referenciam zonas por `ref` do próprio arquivo, nunca por id do banco.
- **Importar = criar um perfil novo.** Se o nome já existe, ganha um número ("Trabalho 2").
- **Segurança na importação:** limite de 2 MB, 200 zonas e 500 automações; tipos e faixas validados; condição, disparo e ação passam pelos mesmos validadores do editor; a mensagem de erro diz o lugar (`automações[2].trigger…`); tudo numa transação (tudo ou nada); **as automações chegam pausadas** para revisão; uma cor de zona inválida é trocada por uma da paleta.
- **Aceite:** exportar → importar → exportar produz o mesmo conteúdo (exceto status, metadados e nome do perfil).

## 6.6 Desempenho, segundo plano e distribuição (Fase 8)
**Medido** em um i5-1334U, com tela de 1920×1080 a 125%:

| Cenário | Resultado |
|---|---|
| Abrir até a janela | ~1 s |
| Parado ou minimizado | 0% de CPU |
| 5 zonas a 20× por segundo + 10 automações (RGB e HSV) | 120 de 120 capturas por zona em 6 s; ~35% de um núcleo |

Otimizações feitas a partir das medições:
1. **Capturas agrupadas:** cada captura no Windows custa ~8 ms fixos, então zonas próximas no mesmo monitor viram uma única captura recortada depois (`plan_batches`).
2. **Ritmo do motor por horário marcado** (25 ms) com `time.sleep` de alta resolução. Antes, o `Event.wait` tinha resolução de ~15,6 ms e o ciclo virava "intervalo + trabalho".
3. **Agendador de zonas por marcação:** a próxima captura fica para "marcação anterior + intervalo", com tolerância de meio ciclo, então desvios não se acumulam.

**Segundo plano:** fechar a janela esconde o app na bandeja (configurável), e a tecla de emergência continua registrada na janela oculta. O app trata `WM_ENDSESSION` (desligamento do Windows e Gerenciador de Reinicialização do instalador) encerrando com segurança.

**Distribuição:**
- PyInstaller em modo pasta, sem UPX, com dados de versão no `.exe`. Plugins e DLLs do Qt que o app não usa ficam de fora: de 144 MB para 87 MB.
- Inno Setup: instalação por usuário (sem UAC), `CloseApplications=force` para atualizar com o app aberto, desinstalação que pergunta sobre os dados. O instalador tem 26 MB.
- `--self-test` valida o executável empacotado (recursos, monitores, captura, janela, tradução).

### Limitações conhecidas (do Windows, não contornáveis sem burlar proteções)
| Situação | Efeito | O que o app faz |
|---|---|---|
| Programa de destino rodando **como administrador** (UIPI) | O Windows descarta os cliques e teclas | Mensagem pedindo para abrir o Vanthor como administrador também |
| Combinação já registrada como **atalho global** por outro programa | A tecla vai para esse programa | O editor avisa ao escolher a combinação |
| Jogos em tela cheia exclusiva / anti-cheat | Captura preta ou entrada ignorada | Não contornamos (fora do escopo, por segurança e termos de uso) |

## 7. Plano de fases
| Fase | Entrega | Critério de aceite |
|---|---|---|
| 1 | Estrutura, identidade, Dashboard, motor com start/pause/stop, emergência, SQLite, logs | App abre, inicia/para pelo botão e pela tecla de emergência, e o histórico persiste |
| 2 | Seleção de regiões arrastando, multi-monitor, DPI | A região desenhada bate pixel a pixel com a captura (teste automatizado) |
| 3 | Detector RGB/HEX/HSV, tolerância, mínimo de pixels/%, conta-gotas | Testes com imagens sintéticas cobrindo os limites de tolerância |
| 4 | Motor de regras simultâneas, frames consecutivos, debounce, transição | Duas regras independentes disparam corretamente em teste |
| 5 | Seletor de ponto, cliques, sequências, teclas | Cliques chegam na coordenada exata em 100% e 125% de escala |
| 6 | Dry Run, cooldown, limite por minuto, sobreposições, monitoramento | O Dry Run nunca move o mouse (teste) |
| 7 | Perfis, importar/exportar JSON, histórico | Exportar e importar preserva as regras byte a byte |
| 8 | Otimização, ícone final, PyInstaller + Inno Setup | O instalador roda em uma máquina limpa e desinstala sem deixar resíduos |
