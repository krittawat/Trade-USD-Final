$ErrorActionPreference = "Stop"

Write-Host "Starting Smoke Test: USC Mode (Cent)"
Write-Host "Setting Environment Variables..."

$env:MODE_ACCOUNT_CURRENCY = "USC"
$env:MODE_LOT = "CENT"
$env:TRADING_MODE = "DRY_RUN"

Write-Host "Launching Bot in DRY_RUN..."
python backend/run_bot.py

# User should check logs for:
# - "mode": "USC"
# - "lot_mode": "CENT" 
# - Symbols with "c" suffix (if MT5 has them)
# - Lot sizes multiplied by 100 in logs (e.g. 1.00 Cent Lot for 0.01 Std risk)
