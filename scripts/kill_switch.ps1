# kill_switch.ps1 — Emergency Stop
# Sends a POST request to the backend to enable the Kill Switch immediately.

$ErrorActionPreference = "Stop"

$API_URL = "http://localhost:8000/api/status/kill-switch"
$BODY = @{
    enabled = $true
} | ConvertTo-Json

Write-Host "============================" -ForegroundColor Red
Write-Host " TRIGGERING KILL SWITCH...  " -ForegroundColor Red
Write-Host "============================" -ForegroundColor Red

try {
    $response = Invoke-RestMethod -Uri $API_URL -Method Post -Body $BODY -ContentType "application/json"
    Write-Host "Success!" -ForegroundColor Green
    Write-Host "Status: $($response.status)" -ForegroundColor Yellow
    Write-Host "Kill Switch: $($response.kill_switch)" -ForegroundColor Yellow
} catch {
    Write-Host "Failed to trigger Kill Switch!" -ForegroundColor Red
    Write-Host $_.Exception.Message
}
