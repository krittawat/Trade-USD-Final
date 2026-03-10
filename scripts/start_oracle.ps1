# start_oracle.ps1
# This script starts the BTC Crypto Oracle Backend on port 8001

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir

# Go to the backend directory
Set-Location -Path $BaseDir

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Starting BTC Crypto Oracle [Port 8001] " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Ensure Uvicorn runs our FastAPI app
python -m uvicorn crypto_oracle.api:app --host 127.0.0.1 --port 8001 --reload
