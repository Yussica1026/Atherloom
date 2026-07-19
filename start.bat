@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
start "" http://127.0.0.1:8876
".venv\Scripts\python.exe" -m uvicorn backend.app:app --host 127.0.0.1 --port 8876
