# ============================================================================
# start_frontend.ps1 — Start Nuxt frontend (Standard Path)
# ============================================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$FRONTEND = Join-Path $ROOT "frontend"

Write-Host "Starting Antigravity Frontend..." -ForegroundColor Cyan
Write-Host "Root: $ROOT"
Write-Host "Frontend Dir: $FRONTEND"

if (!(Test-Path $FRONTEND)) {
    Write-Host "ERROR: Frontend directory not found at $FRONTEND" -ForegroundColor Red
    exit 1
}

Set-Location $FRONTEND

# --- Install deps if needed ---
if (!(Test-Path "node_modules")) {
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    npm install
}

npm run dev
