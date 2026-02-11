# ============================================================================
# qc.ps1 — รัน QC Suite
# ============================================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "Running QC Suite..." -ForegroundColor Cyan
Set-Location (Join-Path $ROOT "backend")
python scripts/verify/qc_suite.py
