@echo off
REM Run Forensic Audit Agent
REM Must be run from project root or via double-click

cd /d %~dp0..\..\..
echo Running Forensic Audit Agent...
echo.

python backend/scripts/forensics/audit_agent.py %*

if %errorlevel% neq 0 (
    echo.
    echo Error occurred!
    pause
    exit /b %errorlevel%
)

echo.
echo Audit complete.
pause
