# ============================================================================
# start_frontend.ps1 — เริ่ม Nuxt frontend
# ============================================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$FRONTEND = Join-Path $ROOT "frontend-USD"

Write-Host "Starting Antigravity Frontend..." -ForegroundColor Cyan
Write-Host "Working Dir: $FRONTEND"

Set-Location $FRONTEND

# --- ตรวจว่า node_modules มีหรือยัง ---
if (!(Test-Path "node_modules")) {
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    npm install
}

npm run dev
