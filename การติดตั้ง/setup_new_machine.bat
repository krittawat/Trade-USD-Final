@echo off
chcp 65001 >nul
title OPUS Trading Engine - New Machine Setup v3.0
color 0B

echo ========================================
echo  OPUS Trading Engine
echo  New Machine Setup Script v3.0
echo  (SQLite Embedded Only)
echo  Updated: 2026-03-02
echo ========================================
echo.

:: --- Determine project root (parent of this script's directory) ---
set "SCRIPT_DIR=%~dp0"
:: Remove trailing backslash
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
:: Go up one level to project root
for %%I in ("%SCRIPT_DIR%") do set "ROOT=%%~dpI"
:: Remove trailing backslash
set "ROOT=%ROOT:~0,-1%"
echo  Project Root: %ROOT%
echo.

:: ==================================================================
:: [1/4] Check Prerequisites
:: ==================================================================
echo [1/4] Checking prerequisites...
echo.

:: --- Python ---
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [FAIL] Python not found!
    echo         Install Python 3.11+ from https://www.python.org/downloads/
    echo         IMPORTANT: Tick "Add Python to PATH" during install!
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo   [OK] %%v

:: --- Git (optional) ---
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [WARN] Git not found (optional, for version control)
) else (
    for /f "tokens=*" %%v in ('git --version 2^>^&1') do echo   [OK] %%v
)

echo.

:: ==================================================================
:: [2/4] Check .env Configuration
:: ==================================================================
echo [2/4] Checking .env configuration...

cd /d "%ROOT%"

if not exist ".env" (
    if exist ".env.example" (
        echo   [WARN] .env not found, copying from .env.example
        copy .env.example .env >nul
        echo.
        echo   ============================================================
        echo   IMPORTANT: You MUST edit .env before starting the system!
        echo   Fill in these values:
        echo     - TRADING_MODE    (start with DRY_RUN!)
        echo     - MT5_LOGIN       (your MT5 account number)
        echo     - MT5_PASSWORD    (your MT5 password)
        echo     - MT5_SERVER      (broker server name)
        echo     - MT5_PATH        (path to terminal64.exe)
        echo     - ACCOUNT_CURRENCY (USC for Cent, USD for Standard)
        echo   ============================================================
        echo.
        echo   Opening .env in notepad...
        notepad .env
        echo   Press any key after saving .env...
        pause >nul
    ) else (
        echo   [FAIL] No .env or .env.example found!
        echo         Make sure you copied the project correctly.
        pause
        exit /b 1
    )
) else (
    echo   [OK] .env exists
)

echo.

:: ==================================================================
:: [3/4] Setup Python Virtual Environment + Dependencies
:: ==================================================================
echo [3/4] Setting up Python environment...

cd /d "%ROOT%"

if not exist ".venv" (
    echo   Creating virtual environment...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo   [FAIL] Failed to create virtual environment!
        pause
        exit /b 1
    )
    echo   [OK] Virtual environment created
) else (
    echo   [OK] Virtual environment already exists
)

echo   Activating venv...
call .venv\Scripts\activate.bat

echo   Installing dependencies (this may take a few minutes)...
pip install MetaTrader5 pandas pandas-ta numpy pydantic pydantic-settings python-dotenv httpx duckdb scikit-learn joblib --quiet 2>nul
if %errorlevel% neq 0 (
    echo   [WARN] Some packages may have failed. Check output above.
    echo         Common fix: Install MetaTrader 5 terminal first for MT5 package.
    echo         Then run: pip install MetaTrader5
) else (
    echo   [OK] Dependencies installed
)

cd /d "%ROOT%"
echo.

:: ==================================================================
:: [4/4] Create Data Directories
:: ==================================================================
echo [4/4] Creating data directories...

if not exist "backend\trader\data" mkdir backend\trader\data
if not exist "backend\trader\logs" mkdir backend\trader\logs
echo   [OK] All directories ready

echo.

:: ==================================================================
:: Verify Installation
:: ==================================================================
echo Verifying installation...
python -c "import MetaTrader5; import pandas; import pandas_ta; print('  [OK] All core imports successful')" 2>nul
if %errorlevel% neq 0 (
    echo   [WARN] Some imports failed. You may need to install MetaTrader5 terminal first.
)

echo.

:: ==================================================================
:: Done!
:: ==================================================================
echo ========================================
echo  Setup Complete!
echo ========================================
echo.
echo  Next steps:
echo.
echo    1. Make sure MetaTrader 5 is OPEN and LOGGED IN
echo    2. Edit .env if needed (MT5_LOGIN, MT5_PASSWORD, MT5_PATH)
echo    3. Run the system:
echo.
echo       cd %ROOT%
echo       .\.venv\Scripts\Activate.ps1
echo       python backend/trader/main.py --mode dry_run
echo.
echo    4. Run QC Suite:
echo       python backend/trader/scripts/qc_suite.py
echo.
echo    5. Run DRY_RUN for 30 min before going LIVE
echo.
echo  For full documentation, see:
echo    %ROOT%\การติดตั้ง\README.md
echo.
echo ========================================
pause
