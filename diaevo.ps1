$ErrorActionPreference = "Stop"

$InstallRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $InstallRoot ".venv\Scripts\python.exe"
$WorkspaceRoot = (Get-Location).Path

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "Python venv not found at $Python. Create it with: uv venv .venv"
    exit 1
}

$env:PYTHONPATH = $InstallRoot
$env:DIAEVO_WORKSPACE = $WorkspaceRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
& $Python -m diaevo.cli @args
exit $LASTEXITCODE
