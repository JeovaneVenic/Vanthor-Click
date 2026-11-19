# Executa o Vanthor Bot Click a partir do código-fonte.
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $root "src"
& (Join-Path $root ".venv\Scripts\python.exe") -m vanthor @args
