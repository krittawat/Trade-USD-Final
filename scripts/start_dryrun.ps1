<#
.SYNOPSIS
    Start Antigravity Bot in DRY_RUN mode (Port 8001)
    Can run alongside LIVE on port 8000/8005

.EXAMPLE
    .\scripts\start_dryrun.ps1
    .\scripts\start_dryrun.ps1 -Port 8001
    .\scripts\start_dryrun.ps1 -Port 8002 -Symbols "XAUUSDc,XAGUSDc"
#>
param (
    [int]$Port = 8001,
    [string]$Symbols
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BACKEND = Join-Path $ROOT "backend"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Antigravity DRY_RUN Mode" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Port:    $Port" -ForegroundColor White
Write-Host "  Mode:    DRY_RUN (no real orders)" -ForegroundColor Yellow
if ($Symbols) {
    Write-Host "  Symbols: $Symbols" -ForegroundColor White
}
Write-Host "  API:     http://localhost:$Port/docs" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $BACKEND

$ArgsList = @("run_bot.py", "--port", $Port, "--mode", "DRY_RUN")

if ($Symbols) {
    $ArgsList += "--symbols"
    $ArgsList += $Symbols
}

Write-Host "Executing: python $ArgsList" -ForegroundColor Yellow
Write-Host ""

python @ArgsList
