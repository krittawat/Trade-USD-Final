# ============================================================================
# dev.ps1 — โหมดพัฒนา (backend + frontend รวม)
# ============================================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "Starting Dev Mode..." -ForegroundColor Cyan

# เริ่ม Backend ด้วย reload
Start-Process powershell -ArgumentList "-File", (Join-Path $ROOT "scripts\start_backend.ps1")

# เริ่ม Frontend
Start-Process powershell -ArgumentList "-File", (Join-Path $ROOT "scripts\start_frontend.ps1")

Write-Host "Dev mode started!" -ForegroundColor Green
Write-Host "  API:       http://localhost:8000/docs" -ForegroundColor Green
Write-Host "  Dashboard: http://localhost:3000" -ForegroundColor Green
