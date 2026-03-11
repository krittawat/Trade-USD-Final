param(
    [string]$Engine = "trader",
    [string]$DataSource = "auto",
    [int]$Days = 60,
    [int]$MinTrades = 3,
    [int]$MaxBarsPerRun = 2000,
    [string]$Symbols = "XAUUSD,BTCUSD,USOIL,USTEC,US30",
    [string]$Timeframes = "M5,M15,H1",
    [string]$DuckDbPath = "backend/data/duckdb/analytics.duckdb",
    [int]$PruneDays = 180,
    [switch]$IncludeShadow
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$PYTHON_BIN = Join-Path $ROOT ".venv\Scripts\python.exe"

if (-not (Test-Path $PYTHON_BIN)) {
    $PYTHON_BIN = "python"
}

$env:PYTHONPATH = $ROOT
$duckDbResolved = $DuckDbPath
if (-not [System.IO.Path]::IsPathRooted($DuckDbPath)) {
    $duckDbResolved = Join-Path $ROOT $DuckDbPath
}

$args = @(
    "-m", "backend.trader.scripts.backtest_matrix_360",
    "--engine", $Engine,
    "--data-source", $DataSource,
    "--days", $Days,
    "--min-trades", $MinTrades,
    "--max-bars-per-run", $MaxBarsPerRun,
    "--symbols", $Symbols,
    "--timeframes", $Timeframes,
    "--duckdb-path", $duckDbResolved,
    "--prune-days", $PruneDays
)

if (-not $IncludeShadow) {
    $args += "--no-shadow"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Backtest Matrix Wrapper" -ForegroundColor Yellow
Write-Host "Root       : $ROOT" -ForegroundColor Gray
Write-Host "Python     : $PYTHON_BIN" -ForegroundColor Gray
Write-Host "PYTHONPATH : $env:PYTHONPATH" -ForegroundColor Gray
Write-Host "Command    : python $($args -join ' ')" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Push-Location $ROOT
try {
    & $PYTHON_BIN @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
