@echo off
chcp 65001 >nul
title Antigravity AI - New Machine Setup

echo ========================================
echo  Antigravity AI Trading System
echo  New Machine Setup Script v2.0
echo  (SQLite + DuckDB — No QuestDB)
echo ========================================
echo.

:: --- Check Prerequisites ---
echo [1/5] Checking prerequisites...

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [FAIL] Python not found! Install Python 3.11+ and add to PATH
    pause
    exit /b 1
)
echo   [OK] Python found

node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [FAIL] Node.js not found! Install Node 18+ LTS
    pause
    exit /b 1
)
echo   [OK] Node.js found

:: --- Check .env ---
echo.
echo [2/5] Checking .env configuration...
if not exist ".env" (
    if exist ".env.example" (
        echo   [WARN] .env not found, copying from .env.example
        copy .env.example .env >nul
        echo   [!] IMPORTANT: Edit .env and fill in MT5_LOGIN, MT5_PASSWORD, MT5_PATH
        echo   Opening .env in notepad...
        notepad .env
        echo   Press any key after saving .env...
        pause >nul
    ) else (
        echo   [FAIL] No .env or .env.example found!
        pause
        exit /b 1
    )
) else (
    echo   [OK] .env exists
)

:: --- Create venv + install backend ---
echo.
echo [3/5] Setting up Python backend...
cd backend

if not exist ".venv" (
    echo   Creating virtual environment...
    python -m venv .venv
)

echo   Activating venv...
call .venv\Scripts\activate.bat

echo   Installing dependencies (this may take a few minutes)...
pip install -e ".[dev]" --quiet
if %errorlevel% neq 0 (
    echo   [WARN] Some packages may have failed. Check output above.
    echo   Common fix: Install MetaTrader 5 terminal first for MT5 package.
) else (
    echo   [OK] Backend dependencies installed
)

cd ..

:: --- Install frontend ---
echo.
echo [4/5] Setting up Frontend...
cd frontend

echo   Installing npm packages...
call npm install --silent
if %errorlevel% neq 0 (
    echo   [WARN] npm install had issues. Try: npm install manually.
) else (
    echo   [OK] Frontend dependencies installed
)

cd ..

:: --- Create data dirs ---
echo.
echo [5/5] Creating data directories...
if not exist "backend\data\sqlite" mkdir backend\data\sqlite
if not exist "backend\data\duckdb" mkdir backend\data\duckdb
if not exist "backend\data\exports" mkdir backend\data\exports
if not exist "backend\logs" mkdir backend\logs
echo   [OK] Directories ready

:: --- Done! ---
echo.
echo ========================================
echo  Setup Complete!
echo ========================================
echo.
echo  Next steps:
echo    1. Make sure MT5 is open and logged in
echo    2. Edit .env if needed (MT5_PATH, MT5_LOGIN)
echo    3. Run backend:
echo       cd backend
echo       .venv\Scripts\activate
echo       python run_bot.py
echo    4. Run frontend:
echo       cd frontend
echo       npm run dev
echo    5. Check: http://localhost:8000/api/health
echo    6. Check: http://localhost:3000
echo    7. DRY_RUN 30 min before going LIVE
echo.
echo ========================================
pause
