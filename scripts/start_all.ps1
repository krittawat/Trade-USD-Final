# ============================================================================
# start_all.ps1 — เริ่มทั้งระบบตามลำดับ
# ============================================================================
# ลำดับ: QuestDB → Backend → QC → Frontend → DRY_RUN
# ============================================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Antigravity AI Trading System" -ForegroundColor Cyan
Write-Host " Starting All Services..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# --- 1. Backend ---
Write-Host "[1/3] Starting Backend (DRY_RUN)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-File", (Join-Path $ROOT "scripts\start_backend.ps1")
Start-Sleep 3

# --- 2. QC Suite ---
Write-Host "[2/3] Running QC Suite..." -ForegroundColor Yellow
$qcResult = & python (Join-Path $ROOT "backend\scripts\verify\qc_suite.py")
Write-Host $qcResult

# --- 3. Frontend ---
Write-Host "[3/3] Starting Frontend..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-File", (Join-Path $ROOT "scripts\start_frontend.ps1")

Write-Host "`n========================================" -ForegroundColor Green
Write-Host " All Services Started!" -ForegroundColor Green
Write-Host " API:       http://localhost:8000" -ForegroundColor Green
Write-Host " Dashboard: http://localhost:3000" -ForegroundColor Green
Write-Host " Mode:      DRY_RUN" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green

