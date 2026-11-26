# Diagnóstico: por que a automação não clicou (ou clicou sem a cor)?

O Vanthor tem dois registros:

- **Histórico** (na tela "Monitor"): o que você precisa saber. Por exemplo, cor detectada, clique feito, erro de captura ou janela cobrindo a zona.
- **Log técnico** (arquivo): o passo a passo do motor, para investigar.

| Sistema | Arquivo do log técnico |
|---|---|
| Windows | `%APPDATA%\VanthorBotClick\logs\vanthor.log` |
| Linux | `~/.local/share/VanthorBotClick/logs/vanthor.log` |

## O que o log técnico registra
- **Início, pausa e parada do motor**, com o motivo da parada (usuário, emergência, erro, fechar o app).
- **Zonas vigiadas:** posição exata em pixels físicos e frequência de leitura.
- **Monitores** conhecidos pelo motor e qualquer mudança neles.
- **Configuração de cada automação:** cor HEX, modo RGB/HSV, tolerância, mínimo de pixels ou %, quadros para confirmar e para sumir, modo de disparo, espera e intervalo mínimo.
- **Resumo de cada automação**, no primeiro quadro e depois a cada minuto:
  - quantos quadros foram analisados;
  - o maior número de pixels com a cor;
  - o **pixel mais parecido com a cor configurada**: a cor HEX lida, a diferença e a posição dentro da zona.
- **Cor detectada**, com quantos pixels; **cor sumiu**; **hora de agir**; **clique** com as coordenadas.
- **Motivo de uma ação não executada:** ponto fora das telas, limite de cliques por minuto, ação anterior ainda na fila, fila parada ou a cor sumiu antes da hora.
- **Falhas de captura**, uma vez por episódio, e quando a captura volta ao normal.
- **Ciclos lentos**, acima de 500 ms, que atrasam todas as zonas.
- **Uma janela do Vanthor sobre uma zona.** Essa aparece também no histórico.

### Como ler o resumo
```
Clique verde [Zona 01]: 600 quadro(s) em 60 s; máximo de 0 de 1200 pixels com a cor;
pixel mais parecido com #22C55E: #22AA5E (diferença 27, tolerância 15) em x+10 y+5; fase waiting
```
O pixel mais parecido ficou a 27 níveis do alvo, e a tolerância é 15, então a cor **não** foi aceita.
Para resolver, ajuste a cor para `#22AA5E` (o Conta-gotas faz isso) ou aumente a tolerância para pelo menos 27.

Se o resumo diz **"nenhum quadro novo"**, a zona não está sendo capturada. Procure as mensagens de falha de captura logo acima.

## Diagnóstico detalhado (cada quadro)
Para registrar **cada quadro analisado**, abra o Vanthor com a variável `VANTHOR_DEBUG=1`. Use só para investigar: o arquivo cresce rápido.

- Windows (PowerShell), com o Vanthor fechado: `$env:VANTHOR_DEBUG=1; & "$env:LOCALAPPDATA\Programs\Vanthor Bot Click\VanthorBotClick.exe"`. Se você instalou para todos os usuários, a pasta é `C:\Program Files\Vanthor Bot Click`.
- Linux: `VANTHOR_DEBUG=1 vanthor-bot-click`

## Causas comuns
| Sintoma | Causa provável | O que fazer |
|---|---|---|
| A cor aparece e nada acontece; o resumo mostra uma diferença maior que a tolerância | A cor na tela é um pouco diferente da cadastrada | Use o Conta-gotas com a cor visível ou aumente a tolerância |
| Aviso "uma janela do Vanthor está por cima da zona" | A captura vê a janela do Vanthor, não o aplicativo | Mova ou minimize a janela do Vanthor |
| Clica sem a cor aparecer | Tolerância alta, HSV com cor acinzentada ou a cor já presente na zona | Na janela "Ajustar cor", veja o que fica colorido com "Destacar pixels encontrados" e reduza a tolerância |
| Só clica na primeira vez | Modo "ao aparecer": só volta a agir quando a cor some por 2 quadros | Normal. Se a cor nunca sai da zona, use o modo contínuo |
| Pisca rápido e não é visto | A cor precisa ficar visível em 2 leituras seguidas da zona | Aumente a frequência da zona (por exemplo, 20 vezes por segundo) |
| "Limite de cliques por minuto atingido" | Proteção contra rajadas | Ajuste em Configurações |
| Janelas de jogos ou vídeo aparecem pretas na captura | O programa desenha fora do caminho de captura padrão | Rode o aplicativo em modo janela ou sem aceleração de hardware |
