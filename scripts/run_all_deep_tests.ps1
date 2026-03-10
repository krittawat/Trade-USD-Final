# run_all_deep_tests.ps1 — Antigravity Deep Evolution Automation

$env:PYTHONPATH = "d:\VibeCode\Trade\backend"
$Symbols = @("BTCUSDm", "XAUUSDm", "XAGUSDm", "USOILm", "US30m", "USTECm")
$Days = 180

Write-Host "`n╔══════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   🧠 ANTIGRAVITY DEEP EVOLUTION BATCH RUNNER                 ║" -ForegroundColor Cyan
Write-Host "║   History: $Days Days (~180 Trading Days)                     ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝`n" -ForegroundColor Cyan

foreach ($Sym in $Symbols) {
    Write-Host ">>> Starting Deep Evolution for $Sym..." -ForegroundColor Yellow
    python -m backend.scripts.deep_evolution_tournament --symbol $Sym --days $Days
    Write-Host ">>> Completed $Sym.`n" -ForegroundColor Green
    Start-Sleep -Seconds 5
}

Write-Host "🏁 All Deep Evolution tournaments completed successfully!" -ForegroundColor Cyan
