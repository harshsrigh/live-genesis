@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Set up Python first using the README.md instructions.
  pause
  exit /b 1
)
echo Answers Over Time, local pages: http://127.0.0.1:8000
echo Read-only. No model calls are started. Ctrl+C stops the server.
echo If the page says the database is not available, run once: .venv\Scripts\python.exe -m live_genesis.cli init
".venv\Scripts\python.exe" -m uvicorn live_genesis.app:app --host 127.0.0.1 --port 8000
pause
