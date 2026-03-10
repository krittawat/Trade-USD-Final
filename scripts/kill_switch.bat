@echo off
setlocal
chcp 65001 >nul
title *** KILL SWITCH *** — Antigravity System
color 4F

echo ===============================================================================
echo  EMERGENCY KILL SWITCH
echo ===============================================================================
echo.
echo  This will IMMEDIATELY STOP:
echo    1. Send kill signal to API (graceful stop)
echo    2. Kill all Python processes (Backend, Scripts)
echo    3. Kill all Node.js processes (Frontend)
echo    4. Switch .env back to DRY_RUN (safe restart)
echo.
echo ===============================================================================
echo  Press ANY KEY to EXECUTE KILL SWITCH...
pause >nul

echo.
echo  [1/4] Sending KILL signal to API...
powershell -Command "try { Invoke-WebRequest -Uri 'http://localhost:8000/api/health/kill-switch' -Method POST -UseBasicParsing -TimeoutSec 3 | Out-Null; Write-Host '    OK - Signal Sent' -ForegroundColor Green } catch { Write-Host '    API Unreachable (Force Kill)' -ForegroundColor Yellow }"

echo.
echo  [2/4] Terminating Python Processes...
taskkill /F /IM python.exe /T 2>nul
if %errorlevel%==0 (
    echo    OK - Python killed.
) else (
    echo    No Python processes found.
)

echo.
echo  [3/4] Terminating Node.js (Frontend)...
taskkill /F /IM node.exe /T 2>nul
if %errorlevel%==0 (
    echo    OK - Node.js killed.
) else (
    echo    No Node.js processes found.
)

echo.
echo  [4/4] Switching .env to DRY_RUN (safe mode)...
powershell -Command "(Get-Content '%~dp0..\.env') -replace 'TRADING_MODE=LIVE', 'TRADING_MODE=DRY_RUN' | Set-Content '%~dp0..\.env'"
echo    OK - Mode set to DRY_RUN.

echo.
echo ===============================================================================
echo  SYSTEM STOPPED
echo ===============================================================================
echo  All trading halted. .env reset to DRY_RUN.
echo  Next startup will be in SAFE mode.
echo.
pause
