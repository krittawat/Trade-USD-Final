@echo off
chcp 65001 >nul
title *** KILL SWITCH ***
color 4F

echo ========================================
echo  *** EMERGENCY KILL SWITCH ***
echo ========================================
echo.
echo  This will IMMEDIATELY stop all trading!
echo.
echo  Press any key to activate...
pause >nul

echo.
echo  Activating Kill Switch...

:: 1. Call API kill-switch
powershell -Command "try { Invoke-WebRequest -Uri 'http://localhost:8000/api/kill-switch' -Method POST -UseBasicParsing -TimeoutSec 5 | Out-Null; Write-Host '  [OK] Kill-switch sent to API' -ForegroundColor Yellow } catch { Write-Host '  [SKIP] API not reachable' -ForegroundColor Gray }"

:: 2. Kill all Python processes
echo  Stopping Python processes...
taskkill /im python.exe /f >nul 2>&1
echo  [OK] Python stopped

echo.
echo ========================================
echo  ALL TRADING STOPPED
echo ========================================
echo.
pause
