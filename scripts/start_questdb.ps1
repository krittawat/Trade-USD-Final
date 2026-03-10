$ErrorActionPreference = "Stop"

$ROOT = "d:\VibeCode\Trade"
$QDB_HOME = Join-Path $ROOT "vendor\questdb\questdb"
$QDB_DATA = Join-Path $ROOT "vendor\questdb\data"
$JAVA_OPTS_1 = "-Xms256m"
$JAVA_OPTS_2 = "-Xmx512m"

Write-Host "Starting QuestDB..."
Write-Host "Home: $QDB_HOME"

$jar = Join-Path $QDB_HOME "questdb.jar"

if (-not (Test-Path $jar)) {
    Write-Host "Error: questdb.jar not found at $jar" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $QDB_DATA | Out-Null

Write-Host "Running Java..."
& java $JAVA_OPTS_1 $JAVA_OPTS_2 -jar $jar -d $QDB_DATA
