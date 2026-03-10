$ErrorActionPreference = "Stop"

Write-Host "Starting Smoke Test: USD Mode (Standard)"
Write-Host "Setting Environment Variables..."

$env:MODE_ACCOUNT_CURRENCY = "USD"
$env:MODE_LOT = "STANDARD"
$env:TRADING_MODE = "DRY_RUN"

# Run Bot for 30 seconds then kill it? 
# Or just run it and let user kill.
# Let's run it.

Write-Host "Launching Bot in DRY_RUN..."
python backend/run_bot.py

# User should check logs for:
# - "mode": "USD"
# - "lot_mode": "STANDARD"
# - Symbols without "c" suffix
