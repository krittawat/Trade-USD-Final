$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $ROOT "start_live.ps1"

Start-Process powershell `
    -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $launcher `
    -WorkingDirectory $ROOT
