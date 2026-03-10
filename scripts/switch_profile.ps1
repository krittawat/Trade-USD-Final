# ============================================================================
# Switch Trading Profile
# Usage: .\scripts\switch_profile.ps1 cent_small
#        .\scripts\switch_profile.ps1 list
# ============================================================================

param(
    [Parameter(Position=0)]
    [string]$ProfileName
)

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ProfilesDir = Join-Path $ProjectRoot "profiles"
$EnvFile = Join-Path $ProjectRoot ".env"

# --- List profiles ---
if ($ProfileName -eq "list" -or [string]::IsNullOrEmpty($ProfileName)) {
    Write-Host "`n📋 Available Profiles:" -ForegroundColor Cyan
    $profiles = Get-ChildItem "$ProfilesDir\*.env" -ErrorAction SilentlyContinue
    if ($profiles) {
        foreach ($p in $profiles) {
            $name = $p.BaseName
            $active = ""
            if (Test-Path $EnvFile) {
                $envContent = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
                $profContent = Get-Content $p.FullName -Raw -ErrorAction SilentlyContinue
                if ($envContent -eq $profContent) { $active = " ← ACTIVE" }
            }
            Write-Host "  • $name$active" -ForegroundColor $(if ($active) { "Green" } else { "White" })
        }
    } else {
        Write-Host "  (no profiles found in $ProfilesDir)" -ForegroundColor Yellow
    }
    Write-Host ""
    exit 0
}

# --- Switch profile ---
$ProfileFile = Join-Path $ProfilesDir "$ProfileName.env"
if (-not (Test-Path $ProfileFile)) {
    Write-Host "❌ Profile '$ProfileName' not found at: $ProfileFile" -ForegroundColor Red
    Write-Host "   Available:" -ForegroundColor Yellow
    Get-ChildItem "$ProfilesDir\*.env" | ForEach-Object { Write-Host "   • $($_.BaseName)" }
    exit 1
}

Copy-Item $ProfileFile $EnvFile -Force
Write-Host "✅ Switched to profile: $ProfileName" -ForegroundColor Green
Write-Host "   Source: $ProfileFile" -ForegroundColor DarkGray
Write-Host "   → .env updated" -ForegroundColor DarkGray
Write-Host ""
Write-Host "🔄 Restart bot to apply:" -ForegroundColor Yellow
Write-Host "   python run_bot.py --mode LIVE --profile $ProfileName" -ForegroundColor White
