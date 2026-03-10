@echo off
setlocal
chcp 65001 >nul
title Antigravity AI — LIVE MODE (All Strategies)
color 0A

echo ========================================
echo   ANTIGRAVITY AI TRADING SYSTEM
echo   MODE: *** LIVE ***  ALL STRATEGIES
echo ========================================
echo.
echo   Account: Exness CENT (USC)
echo   Symbols: XAUUSDc, XAGUSDc, EURUSDc, USDJPYc, BTCUSDc
echo   Shadow Mode: ENABLED (Learning in background)
echo.
echo   Risk Controls:
echo     - Max 2%% per trade
echo     - SL mandatory
echo     - Capital floor 90%%
echo     - Break-Even at +1R
echo.
echo ========================================
echo.

:: --- Set LIVE mode ---
set TRADING_MODE=LIVE

:: --- Kill existing processes on port 8000 ---
echo [1/3] Clearing port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul
echo      Done.

:: --- Start Backend ---
echo.
echo [2/3] Starting Backend (LIVE MODE)...
cd /d "%~dp0..\backend"
start "Antigravity LIVE" cmd /k "set TRADING_MODE=LIVE && cd /d %~dp0..\backend && python run_bot.py"

:: --- Wait for backend ---
echo      Waiting for backend startup...
timeout /t 8 /nobreak >nul

:: --- Health Check ---
echo.
echo [3/3] Health Check...
powershell -Command "try { $r = Invoke-RestMethod -Uri 'http://localhost:8000/api/health' -TimeoutSec 5; Write-Host ('      Mode: ' + $r.mode) -ForegroundColor Green; Write-Host ('      MT5: ' + $r.mt5.status) -ForegroundColor Green; Write-Host ('      Strategies: ' + $r.strategies) -ForegroundColor Green } catch { Write-Host '      WARNING: Backend not responding yet' -ForegroundColor Yellow }"

echo.
echo ========================================
echo   LIVE TRADING ACTIVE
echo ========================================
echo.
echo   Dashboard: http://localhost:3000
echo   API Docs:  http://localhost:8000/docs
echo   Scoreboard: http://localhost:8000/api/shadow/scoreboard
echo.
echo   To STOP: Run kill_switch.bat
echo   To check: http://localhost:8000/api/health
echo.
pause
