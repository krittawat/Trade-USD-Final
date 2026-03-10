param (
    [int]$Port = 8000,
    [string]$Mode,
    [string]$Profile,
    [string]$Symbols
)

$ErrorActionPreference = "Stop"
# Go up one level from scripts folder
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BACKEND = Join-Path $ROOT "backend"

Write-Host "Starting Antigravity Bot on Port $Port..." -ForegroundColor Cyan
Write-Host "Working Dir: $BACKEND"

Set-Location $BACKEND

$ArgsList = @()

if ($Port) {
    $ArgsList += "--port"
    $ArgsList += $Port
}

if ($Mode) {
    $ArgsList += "--mode"
    $ArgsList += $Mode
}

if ($Profile) {
    $ArgsList += "--profile"
    $ArgsList += $Profile
}

if ($Symbols) {
    $ArgsList += "--symbols"
    $ArgsList += $Symbols
}

Write-Host "Executing: python run_bot.py $ArgsList" -ForegroundColor Yellow

python run_bot.py @ArgsList
