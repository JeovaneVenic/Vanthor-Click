; Instalador do Vanthor Bot Click (Inno Setup 6.7+).
; Gerado pelo scripts\build.ps1, que passa /DAppVersion, /DSourceDir e /DOutputDir.
;
; Escolhas:
; - Instalação por usuário (sem administrador): menos atrito e nada de UAC para instalar/atualizar.
; - AppId fixo: uma versão nova instala POR CIMA da antiga e preserva os dados do usuário.
; - Tudo que o app precisa vai DENTRO do instalador (Python, Qt, NumPy, runtime do Visual C++):
;   nada é baixado, então a instalação funciona sem internet e não quebra por firewall/antivírus.
;   No fim, o próprio Vanthor faz um autoteste neste computador e o resultado aparece na tela.
; - Visual: tema escuro do Windows 11 com a pantera, sem linhas divisórias, textos curtos em PT-BR.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\VanthorBotClick"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\Instalador"
#endif

#define AppName "Vanthor Bot Click"
#define AppExe "VanthorBotClick.exe"
#define DataFolder "VanthorBotClick"

[Setup]
AppId={{8F3C2A71-5B6E-4D2A-9C1F-7A4E2B9D6C10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Vanthor
AppCopyright=© 2026 Vanthor
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription=Instalador do {#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
; Mostra a tela de boas-vindas (o Inno 6 a esconde por padrão): é onde a pessoa entende o que instala.
DisableWelcomePage=no
DisableReadyPage=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=VanthorBotClick-Setup-{#AppVersion}
SetupIconFile=..\..\src\vanthor\resources\brand\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
; Visual: escuro sempre (combina com o app), estilo Windows 11, sem linhas divisórias,
; barra de título também escura. Fundo no mesmo grafite do aplicativo.
WizardStyle=modern dark windows11 hidebevels includetitlebar
WizardBackColor=#101214
WizardImageFile=wizard-164.bmp,wizard-246.bmp,wizard-328.bmp
WizardSmallImageFile=wizard-small-55.bmp,wizard-small-83.bmp,wizard-small-110.bmp
WizardImageBackColor=#101214
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 2004+: necessário para as sobreposições invisíveis à captura.
MinVersion=10.0.19041
; Atualização com o app aberto: o Gerenciador de Reinicialização do Windows fecha o Vanthor sozinho.
; O app trata o pedido de fim de sessão (WM_ENDSESSION) parando as automações com segurança.
CloseApplications=force
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Messages]
ptbr.WelcomeLabel1=Bem-vindo ao [name]
ptbr.WelcomeLabel2=O Vanthor observa áreas da sua tela e clica ou digita por você quando uma cor aparece.%n%nA instalação leva menos de um minuto e não precisa de internet: tudo o que o aplicativo usa já vem neste instalador.
ptbr.WizardSelectTasks=Atalhos
ptbr.SelectTasksDesc=Onde você quer encontrar o Vanthor?
ptbr.SelectTasksLabel2=O Vanthor sempre aparece no menu Iniciar. Escolha se quer também um atalho na Área de Trabalho e clique em Avançar.
ptbr.WizardReady=Pronto para instalar
ptbr.ReadyMemoTasks=Atalhos:
ptbr.WizardInstalling=Instalando
ptbr.StatusExtractFiles=Copiando o Vanthor e tudo o que ele precisa para funcionar...
ptbr.StatusCreateIcons=Criando atalhos...
ptbr.StatusSavingUninstall=Preparando a desinstalação limpa...
ptbr.StatusClosingApplications=Fechando o Vanthor para atualizar...
ptbr.ClickNext=Clique em Avançar para continuar.
ptbr.ReadyLabel1=Tudo pronto para instalar o [name].
ptbr.ReadyLabel2a=Clique em Instalar. Você vai acompanhar cada etapa na próxima tela.
ptbr.InstallingLabel=Aguarde enquanto o [name] é instalado. Você pode acompanhar cada etapa abaixo.
ptbr.FinishedHeadingLabel=O Vanthor está pronto
ptbr.FinishedLabelNoIcons=O [name] foi instalado e verificado neste computador.
ptbr.FinishedLabel=O [name] foi instalado e verificado neste computador.%n%nPara começar: marque uma Zona, crie uma Automação e use "Testar sem clicar" para ver onde ele clicaria.%n%nAo fechar a janela, o Vanthor continua rodando no ícone perto do relógio. Esc para tudo na hora.
ptbr.ClickFinish=Clique em Concluir para sair.

[Tasks]
Name: "desktopicon"; Description: "Atalho na Área de Trabalho"

[InstallDelete]
; Atualização limpa: remove as bibliotecas da versão anterior antes de copiar as novas.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Automação visual da tela"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; Comment: "Automação visual da tela"

[Registry]
; Só remove (na desinstalação) o "Iniciar com o Windows", se o usuário tiver ligado no app.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "VanthorBotClick"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir o {#AppName} agora"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  SelfTestOk: Boolean;
  SelfTestRan: Boolean;

{ Mostra em palavras o que está acontecendo, para a pessoa acompanhar a instalação. }
procedure ShowStep(const Text: String);
begin
  WizardForm.StatusLabel.Caption := Text;
  WizardForm.StatusLabel.Repaint;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
    ShowStep('Copiando o Vanthor e tudo o que ele precisa para funcionar...');

  if CurStep = ssPostInstall then
  begin
    ShowStep('Criando atalhos...');
    { Verificação real: o próprio Vanthor confere tela, captura, recursos e janela, sem abrir nada. }
    ShowStep('Verificando se tudo funciona neste computador...');
    WizardForm.ProgressGauge.Style := npbstMarquee;
    SelfTestRan := Exec(ExpandConstant('{app}\{#AppExe}'), '--self-test', '', SW_HIDE,
                        ewWaitUntilTerminated, ResultCode);
    SelfTestOk := SelfTestRan and (ResultCode = 0);
    WizardForm.ProgressGauge.Style := npbstNormal;
    if SelfTestOk then
      ShowStep('Tudo certo. Finalizando...')
    else
      ShowStep('A verificação encontrou um problema. Veja os detalhes na próxima tela.');
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and SelfTestRan and (not SelfTestOk) then
    WizardForm.FinishedLabel.Caption :=
      'O Vanthor foi instalado, mas a verificação neste computador não passou.' + #13#10#13#10 +
      'O relatório está em:' + #13#10 + ExpandConstant('{userappdata}\{#DataFolder}\self-test.json') + #13#10#13#10 +
      'Tente abrir o Vanthor mesmo assim. Se não abrir, reinicie o computador e instale de novo.';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
  begin
    if MsgBox('Remover também suas zonas, automações, perfis e histórico?' + #13#10#13#10 +
              'Escolha Não para manter tudo e recuperar ao reinstalar.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{userappdata}\{#DataFolder}'), True, True, True);
  end;
end;
