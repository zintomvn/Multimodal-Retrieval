@echo off
cd /d "%~dp0apps\backend"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -c "from alembic.config import main; main()" upgrade head
