# Script to run Trader with PnL Reset
# Fix: Using English to avoid PowerShell encoding parsing issues
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$TRADER_DIR = Join-Path $ROOT "backend\trader"
$VENV_PY = Join-Path $ROOT ".venv\Scripts\python.exe"
$env:PYTHONPATH = Join-Path $ROOT "backend"

Write-Host "--- Stopping old Trader processes ---" -ForegroundColor Cyan
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and (
        $_.CommandLine -match "backend\.trader\.main" -or
        $_.CommandLine -match "backend[\\/]+trader[\\/]+main\.py"
    )
} | ForEach-Object {
    Write-Host "Killing PID: $($_.ProcessId)" -ForegroundColor Yellow
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2

Write-Host "--- Starting ANTIGRAVITY (LIVE + RESET PNL) ---" -ForegroundColor Green
if (Test-Path $VENV_PY) {
    $PYTHON_BIN = $VENV_PY
} else {
    $PYTHON_BIN = "python"
}

Set-Location $TRADER_DIR
& $PYTHON_BIN "main.py" --mode live --reset-pnl
