# ============================================================================
# start_backend.ps1 — เริ่ม FastAPI backend
# ============================================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BACKEND = Join-Path $ROOT "backend"

Write-Host "Starting Antigravity Backend..." -ForegroundColor Cyan
Write-Host "Working Dir: $BACKEND"

# --- เปิด FastAPI ด้วย uvicorn ---
Set-Location $BACKEND
python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8000
