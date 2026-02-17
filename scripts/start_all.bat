@echo off
chcp 65001 >nul
title Antigravity AI Trading System

echo ========================================
echo  Antigravity AI Trading System
echo  Starting All Services...
echo ========================================

:: --- Set root directory ---
set "ROOT=%~dp0.."

:: --- 1. Start Backend (Bot + API) ---
echo.
echo [1/2] Starting Backend...
cd /d "%ROOT%\backend"
start "Antigravity Bot" cmd /k "python run_bot.py"
timeout /t 5 /nobreak >nul

:: --- 2. Health Check ---
echo [2/2] Checking health...
timeout /t 3 /nobreak >nul
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:8000/api/health' -UseBasicParsing -TimeoutSec 5; $j = $r.Content | ConvertFrom-Json; Write-Host ('  Status: ' + $j.status) -ForegroundColor Green; Write-Host ('  Mode:   ' + $j.mode) -ForegroundColor Green; Write-Host ('  MT5:    ' + $j.services.mt5) -ForegroundColor Green; Write-Host ('  Strats: ' + $j.strategies_registered) -ForegroundColor Green } catch { Write-Host '  API not ready yet - wait a moment' -ForegroundColor Yellow }"

echo.
echo ========================================
echo  All Services Started!
echo  API:       http://localhost:8000
echo  Swagger:   http://localhost:8000/docs
echo  Health:    http://localhost:8000/api/health
echo ========================================
echo.
echo  KILL SWITCH: Press any key to STOP all trading
echo  (or call POST http://localhost:8000/api/kill-switch)
echo ========================================
pause >nul

:: --- KILL SWITCH activated ---
echo.
echo  *** KILL SWITCH ACTIVATED ***
powershell -Command "try { Invoke-WebRequest -Uri 'http://localhost:8000/api/kill-switch' -Method POST -UseBasicParsing -TimeoutSec 5 | Out-Null; Write-Host '  Kill-switch sent to API' -ForegroundColor Red } catch { Write-Host '  API not reachable' -ForegroundColor Yellow }"
echo  Stopping all Python processes...
taskkill /im python.exe /f >nul 2>&1
echo  All stopped.
echo.
pause
