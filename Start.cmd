@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Set up Python first using the README.md instructions.
  pause
  exit /b 1
)
echo LIVE Genesis observatory: http://127.0.0.1:8000
echo Read-only human access. No model calls are started. Ctrl+C stops the server.
".venv\Scripts\python.exe" -m uvicorn live_genesis.app:app --host 127.0.0.1 --port 8000
pause
