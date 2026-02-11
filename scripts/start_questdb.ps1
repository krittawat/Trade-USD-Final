# ============================================================================
# start_questdb.ps1 — เริ่ม QuestDB แบบ native (ไม่ใช้ Docker)
# ============================================================================
# ใช้ QuestDB ที่ดาวน์โหลดเป็น zip แล้วแตกไว้ที่ vendor/questdb/
# JVM heap จำกัดเพื่อรองรับเครื่อง 8GB RAM
# ============================================================================

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$QDB_HOME = Join-Path $ROOT "vendor\questdb\questdb"
$QDB_DATA = Join-Path $ROOT "vendor\questdb\data"

# --- JVM heap limits (8GB machine) ---
$env:JAVA_OPTS = "-Xms256m -Xmx512m"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Starting QuestDB (Native, No Docker)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "QDB_HOME : $QDB_HOME"
Write-Host "QDB_DATA : $QDB_DATA"
Write-Host "JAVA_OPTS: $env:JAVA_OPTS"
Write-Host ""

# --- ตรวจว่ามี questdb.jar หรือไม่ ---
$jar = Join-Path $QDB_HOME "questdb.jar"
if (!(Test-Path $jar)) {
    Write-Host "ERROR: questdb.jar not found at: $jar" -ForegroundColor Red
    Write-Host ""
    Write-Host "วิธีแก้:" -ForegroundColor Yellow
    Write-Host "  1. ดาวน์โหลด QuestDB จาก https://questdb.io/download/" -ForegroundColor Yellow
    Write-Host "  2. แตก zip ไว้ที่: $QDB_HOME" -ForegroundColor Yellow
    Write-Host "  3. ตรวจว่ามี questdb.jar ในโฟลเดอร์" -ForegroundColor Yellow
    exit 1
}

# --- สร้างโฟลเดอร์ data ---
New-Item -ItemType Directory -Force -Path $QDB_DATA | Out-Null

# --- เริ่ม QuestDB ---
Write-Host "Starting QuestDB on ports: HTTP=9000, ILP=9009, PG=8812" -ForegroundColor Green
& java $env:JAVA_OPTS -jar $jar -d $QDB_DATA
