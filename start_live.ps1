# ==============================================================================
# ANTIGRAVITY LIVE TRADING STARTUP SCRIPT
# ==============================================================================
# This script ensures a clean state by termination any existing trader processes
# before starting the system in LIVE mode.
# ==============================================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$TRADER_DIR = Join-Path $ROOT "backend\trader"
$VENV_PY = Join-Path $ROOT ".venv\Scripts\python.exe"
$env:PYTHONPATH = Join-Path $ROOT "backend"

Write-Host "Stopping existing Python trader processes..." -ForegroundColor Yellow

# Kill existing bot processes (module mode + script mode)
Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq "python.exe" -and (
            $_.CommandLine -match "backend\.trader\.main" -or
            $_.CommandLine -match "backend[\\/]+trader[\\/]+main\.py"
        )
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Start-Sleep -Seconds 2

Write-Host "Starting ANTIGRAVITY in LIVE MODE..." -ForegroundColor Green
if (Test-Path $VENV_PY) {
    $PYTHON_BIN = $VENV_PY
} else {
    $PYTHON_BIN = "python"
}

Set-Location $TRADER_DIR
& $PYTHON_BIN "main.py" --mode live
