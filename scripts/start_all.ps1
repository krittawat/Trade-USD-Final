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

# --- 1. QuestDB ---
Write-Host "`n[1/4] Starting QuestDB..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-File", (Join-Path $ROOT "scripts\start_questdb.ps1")
Start-Sleep 5

# --- 2. Backend ---
Write-Host "[2/4] Starting Backend (DRY_RUN)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-File", (Join-Path $ROOT "scripts\start_backend.ps1")
Start-Sleep 3

# --- 3. QC Suite ---
Write-Host "[3/4] Running QC Suite..." -ForegroundColor Yellow
$qcResult = & python (Join-Path $ROOT "backend\scripts\verify\qc_suite.py")
Write-Host $qcResult

# --- 4. Frontend ---
Write-Host "[4/4] Starting Frontend..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-File", (Join-Path $ROOT "scripts\start_frontend.ps1")

Write-Host "`n========================================" -ForegroundColor Green
Write-Host " All Services Started!" -ForegroundColor Green
Write-Host " API:       http://localhost:8000" -ForegroundColor Green
Write-Host " Dashboard: http://localhost:3000" -ForegroundColor Green
Write-Host " QuestDB:   http://localhost:9000" -ForegroundColor Green
Write-Host " Mode:      DRY_RUN" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
