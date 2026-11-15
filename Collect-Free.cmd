@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Set up Python first using the README.md instructions.
  pause
  exit /b 1
)
echo Free-only OpenRouter collector. Nothing is asked of any model until you type the confirmation it prints.
echo Add --preview-only to see the plan without a key and without any call.
".venv\Scripts\python.exe" -m live_genesis.operator_launcher %*
pause
