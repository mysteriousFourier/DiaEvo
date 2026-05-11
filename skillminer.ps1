$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "Python venv not found at $Python. Create it with: uv venv .venv"
    exit 1
}

$env:PYTHONPATH = $ProjectRoot
Push-Location $ProjectRoot
try {
    & $Python -m skillminer.cli @args
} finally {
    Pop-Location
}
exit $LASTEXITCODE
