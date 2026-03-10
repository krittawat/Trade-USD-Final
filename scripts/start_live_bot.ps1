# ============================================================================
# start_live_bot.ps1 — รัน ANTIGRAVITY LIVE BOT แบบถาวร (Auto-Restart)
# Usage: .\scripts\start_live_bot.ps1
# ============================================================================

$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$env:PYTHONPATH = Join-Path $ROOT "backend"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  🚀 ANTIGRAVITY LIVE BOT — AUTO-RESTART MODE" -ForegroundColor Yellow
Write-Host "  Root  : $ROOT" -ForegroundColor Gray
Write-Host "  PYTHONPATH: $env:PYTHONPATH" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $ROOT

$restartCount = 0
$maxRestarts  = 50   # ป้องกัน crash loop ถาวร

while ($restartCount -lt $maxRestarts) {
    $restartCount++
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

    Write-Host "[$timestamp] 🟢 Starting Bot... (Attempt #$restartCount)" -ForegroundColor Green

    # --- Run Bot ---
    uv run python -m backend.trader.main --mode live

    $exitCode = $LASTEXITCODE
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

    if ($exitCode -eq 0) {
        Write-Host "[$timestamp] ✅ Bot exited cleanly (code 0). Restarting in 5s..." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    } elseif ($exitCode -eq 99) {
        # Exit code 99 = Kill-Switch triggered (intentional halt)
        Write-Host "[$timestamp] 🛑 KILL-SWITCH triggered (code 99). NOT restarting." -ForegroundColor Red
        Write-Host "    แก้ปัญหาก่อน แล้วรัน script ใหม่ครับ" -ForegroundColor Red
        break
    } else {
        Write-Host "[$timestamp] ⚠️  Bot crashed (code $exitCode). Restarting in 10s..." -ForegroundColor Red
        Start-Sleep -Seconds 10
    }
}

Write-Host ""
Write-Host "🔴 MAX RESTARTS ($maxRestarts) reached. Bot stopped." -ForegroundColor Red
Write-Host "    ตรวจสอบ logs\error.log แล้วรัน script ใหม่ครับ" -ForegroundColor Yellow
