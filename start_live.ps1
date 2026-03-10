# ==============================================================================
# ANTIGRAVITY LIVE TRADING STARTUP SCRIPT
# ==============================================================================
# This script ensures a clean state by termination any existing trader processes
# before starting the system in LIVE mode.
# ==============================================================================

Write-Host "Stopping existing Python trader processes..." -ForegroundColor Yellow

# Kill existing processes
Get-Process -Name python -ErrorAction SilentlyContinue | 
    Where-Object { $_.CommandLine -match 'trader.main' } | 
    Stop-Process -Force

Start-Sleep -Seconds 2

Write-Host "Starting ANTIGRAVITY in LIVE MODE..." -ForegroundColor Green
uv run python -m backend.trader.main --mode live
