# Gera o instalador do Vanthor Bot Click: testes -> executável -> autoteste do .exe -> instalador -> SHA-256.
#
#   .\scripts\build.ps1              (completo)
#   .\scripts\build.ps1 -SkipTests   (pula a suíte de testes; o autoteste do .exe sempre roda)
#
# O executável é montado em %LOCALAPPDATA%\VanthorBuild, FORA da pasta do projeto: a Área de
# Trabalho está no OneDrive, que trava arquivos durante a sincronização e quebra o empacotamento.
param([switch]$SkipTests)

# "Continue": ferramentas como o PyInstaller escrevem avisos na saída de erro; no PowerShell 5.1
# isso viraria falha. O sucesso de cada etapa é conferido pelo código de saída ($LASTEXITCODE).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$version = (Select-String -Path (Join-Path $root "src\vanthor\__init__.py") -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$work = Join-Path $env:LOCALAPPDATA "VanthorBuild"
$dist = Join-Path $work "dist\VanthorBotClick"
$release = Join-Path $root "Instalador"

Write-Host "Vanthor Bot Click $version" -ForegroundColor Cyan

if (-not $SkipTests) {
    Write-Host "1/4 Testes (movem o mouse de verdade: não use o computador enquanto rodam)..."
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Testes falharam. Nada foi gerado." }
}

Write-Host "2/4 Executável (PyInstaller)..."
& $python -m PyInstaller --noconfirm --clean --log-level WARN --distpath (Join-Path $work "dist") --workpath (Join-Path $work "build") (Join-Path $root "packaging\vanthor.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou." }

Write-Host "3/4 Autoteste do executável..."
$selfTestDir = Join-Path $work ("selftest-" + [DateTime]::Now.ToString("yyyyMMddHHmmss"))
$env:VANTHOR_DATA_DIR = $selfTestDir
try {
    $process = Start-Process -FilePath (Join-Path $dist "VanthorBotClick.exe") -ArgumentList "--self-test" -Wait -PassThru
} finally {
    Remove-Item Env:VANTHOR_DATA_DIR
}
if ($process.ExitCode -ne 0) { throw "O autoteste do executável falhou. Veja $selfTestDir\self-test.json" }

Write-Host "4/4 Instalador (Inno Setup)..."
$iscc = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 não encontrado. Instale com: winget install JRSoftware.InnoSetup --scope user" }
New-Item -ItemType Directory -Force $release | Out-Null
& $iscc /Q "/DAppVersion=$version" "/DSourceDir=$dist" "/DOutputDir=$release" (Join-Path $root "packaging\installer\vanthor.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup falhou." }

$setup = Join-Path $release "VanthorBotClick-Setup-$version.exe"
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
"$hash  VanthorBotClick-Setup-$version.exe" | Set-Content "$setup.sha256" -Encoding ascii
$sizeMb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
Write-Host "Pronto: $setup ($sizeMb MB)" -ForegroundColor Green
Write-Host "SHA-256: $hash"
