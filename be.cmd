@echo off
cd /d "%~dp0apps\backend"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
