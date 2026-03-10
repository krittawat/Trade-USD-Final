@echo off
cd /d "%~dp0\..\backend"
call .venv\Scripts\activate.bat
echo Starting ANTIGRAVITY TICK RECORDER (SQLITE)...
python scripts/tick_recorder_sqlite.py
pause
