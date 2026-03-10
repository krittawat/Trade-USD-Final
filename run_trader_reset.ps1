# Script to run Trader with PnL Reset
# Fix: Using English to avoid PowerShell encoding parsing issues
$env:PYTHONPATH = ".;backend"

Write-Host "--- Stopping old Trader processes ---" -ForegroundColor Cyan
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*trader.main*" } | ForEach-Object { 
    Write-Host "Killing PID: $($_.ProcessId)" -ForegroundColor Yellow
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2

Write-Host "--- Starting ANTIGRAVITY (LIVE + RESET PNL) ---" -ForegroundColor Green
uv run python -m backend.trader.main --mode live --reset-pnl
