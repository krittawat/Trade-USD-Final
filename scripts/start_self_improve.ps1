# ============================================================================
# start_self_improve.ps1 — รัน AI Self-Improvement Daemon แบบถาวร
#
# ทำหน้าที่:
#   - รัน Training Session ทุก 6 ชั่วโมง (หรือตามที่กำหนด)
#   - Auto-restart ถ้า script crash
#   - Log ผลลัพธ์ไว้ใน logs/self_improve.log
#
# Usage:
#   .\scripts\start_self_improve.ps1               (ทุก 6 ชั่วโมง)
#   .\scripts\start_self_improve.ps1 -Interval 3   (ทุก 3 ชั่วโมง)
#   .\scripts\start_self_improve.ps1 -Once         (รันครั้งเดียว)
# ============================================================================

param(
    [float]$Interval = 6.0,
    [switch]$Once = $false
)

$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$env:PYTHONPATH = Join-Path $ROOT "backend"
$LogFile = Join-Path $ROOT "backend\logs\self_improve.log"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  🧠 ANTIGRAVITY AI — SELF-IMPROVEMENT DAEMON" -ForegroundColor Yellow
Write-Host "  Interval : Every ${Interval}h" -ForegroundColor Gray
Write-Host "  PYTHONPATH: $env:PYTHONPATH" -ForegroundColor Gray
Write-Host "  Log: $LogFile" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $ROOT

$restartCount = 0
$args_list = @("--interval", $Interval)
if ($Once) { $args_list += "--once" }

while ($true) {
    $restartCount++
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] 🧠 Starting Self-Improve Session... (Attempt #$restartCount)" -ForegroundColor Green

    # รัน self_improve_loop พร้อม log ไฟล์
    uv run python -m backend.scripts.self_improve_loop @args_list 2>&1 | Tee-Object -Append -FilePath $LogFile

    $exitCode = $LASTEXITCODE
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

    if ($Once -or $exitCode -eq 0) {
        Write-Host "[$timestamp] ✅ Self-improve run complete." -ForegroundColor Green
        break
    } else {
        Write-Host "[$timestamp] ⚠️  Self-improve crashed (code $exitCode). Restarting in 30s..." -ForegroundColor Red
        Start-Sleep -Seconds 30
    }
}
