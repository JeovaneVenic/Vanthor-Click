# Publicar o Vanthor Bot Click

## 1. Gerar a versão
1. Atualize a versão em `src/vanthor/__init__.py` (`__version__`) e em `pyproject.toml`.
2. Rode `.\scripts\build.ps1` sem `-SkipTests`. Não use o computador enquanto os testes de
   entrada real rodam (eles movem o mouse).
3. O resultado fica em `Instalador\`: o instalador e um arquivo `.sha256` com a impressão digital.

O instalador usa um **AppId fixo**: a versão nova instala por cima da antiga, fecha o app sozinha
se estiver aberto e preserva os dados do usuário. **Nunca mude o AppId**, senão o Windows passa a
tratar como outro programa.

## 2. Assinar o instalador (fortemente recomendado)
Sem assinatura digital, o Windows mostra **"Editor desconhecido"** e o SmartScreen avisa
"O Windows protegeu o computador". Muitos usuários desistem nesse ponto, e programas de
automação de mouse e teclado empacotados em Python costumam receber **falsos positivos** de antivírus.

| Opção | Custo aproximado | Efeito |
|---|---|---|
| Certificado OV (Organization Validation) | US$ 200 a 400 por ano | Mostra seu nome; o SmartScreen confia aos poucos, com a reputação |
| Certificado EV ou Azure Trusted Signing | US$ 10 por mês (Azure) a US$ 400+ por ano | Reputação mais rápida no SmartScreen |

Assine **os dois** arquivos: o executável (antes do instalador) e o instalador. Exemplo com o
`signtool` do Windows SDK:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a "Instalador\VanthorBotClick-Setup-1.0.0.exe"
```

> Para assinar também o `VanthorBotClick.exe` dentro do instalador, assine-o em
> `%LOCALAPPDATA%\VanthorBuild\dist\VanthorBotClick\` entre as etapas 3 e 4 do `build.ps1`.

## 3. Antes de enviar a um site de download
- Envie o instalador ao **VirusTotal** e guarde o link do relatório.
- Em caso de falso positivo, envie para análise nos portais dos fabricantes (Microsoft Defender:
  *Submit a file*; e os portais de envio de falso positivo de cada antivírus que acusar).
- Publique junto o **SHA-256** (arquivo `.sha256`), para o usuário conferir o download.
- Teste numa máquina limpa ou numa VM com Windows 10 2004+ e Windows 11: instalar, abrir,
  "Testar sem clicar", desinstalar.

## 4. Texto sugerido para a página de download
> **Vanthor Bot Click**: automação visual para Windows. Marque uma área da tela, escolha a cor e
> o Vanthor clica ou digita por você quando ela aparecer. Várias automações ao mesmo tempo, modo de
> teste que mostra onde clicaria, tecla de emergência e limite de cliques. 100% local.
> Requer Windows 10 (2004+) ou 11, 64 bits.

## 5. Atualizações futuras
Hoje a atualização é manual: o usuário baixa e executa o instalador novo, que instala por cima.
Uma verificação automática de versões exigiria um endereço na internet com a versão mais nova
(por exemplo, um arquivo JSON num site ou nas Releases do GitHub). Isso fica para uma versão
futura, porque hoje o app não acessa a internet de propósito.
