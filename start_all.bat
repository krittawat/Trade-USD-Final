@echo off
chcp 65001 >nul
title Antigravity AI Trading System - Launcher

echo ========================================
echo  Antigravity AI Trading System
echo  Starting All Services...
echo ========================================

:: --- 1. Start QuestDB (New Window) ---
echo.
echo [1/3] Starting QuestDB...
start "QuestDB" powershell -NoExit -ExecutionPolicy Bypass -File scripts/start_questdb.ps1
timeout /t 5 /nobreak >nul

:: --- 2. Start Backend (New Window) ---
echo.
echo [2/3] Starting Backend (DRY_RUN - ALL SYMBOLS)...
start "Antigravity Backend" cmd /k "python backend/run_bot.py --mode DRY_RUN --symbols ALL"
timeout /t 5 /nobreak >nul

:: --- 3. Start Frontend (New Window) ---
echo.
echo [3/3] Starting Frontend...
cd frontend-USD
start "Antigravity Frontend" cmd /k "npm run dev"
cd ..

:: --- 4. Health Check & Kill Switch ---
echo.
echo ========================================
echo  All Services Launching!
echo  API:       http://localhost:8000
echo  Swagger:   http://localhost:8000/docs
echo  Frontend:  http://localhost:3000
echo ========================================
echo.
echo  Press any key to ACTIVATE KILL SWITCH (Stop All)
echo ========================================
pause >nul

:: --- KILL SWITCH ---
echo.
echo  *** KILL SWITCH ACTIVATED ***
powershell -Command "try { Invoke-WebRequest -Uri 'http://localhost:8000/api/kill-switch' -Method POST -UseBasicParsing -TimeoutSec 2 | Out-Null; Write-Host '  Kill-switch sent to API' -ForegroundColor Red } catch { Write-Host '  API not reachable' -ForegroundColor Yellow }"

echo  Stopping Python processes...
taskkill /im python.exe /f >nul 2>&1
echo  Stopping Node processes...
taskkill /im node.exe /f >nul 2>&1
echo  Stopping Java (QuestDB)...
taskkill /im java.exe /f >nul 2>&1

echo.
echo  All services stopped.
pause
