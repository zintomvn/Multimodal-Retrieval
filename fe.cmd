@echo off
cd /d "%~dp0apps\web"

set VITE_API_BASE_URL=http://localhost:8010

npm run dev