@echo off
cd /d "%~dp0apps\backend"
python -c "from alembic.config import main; main()" upgrade head
