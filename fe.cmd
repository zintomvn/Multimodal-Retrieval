@echo off
cd /d "%~dp0apps\web"
set VITE_API_BASE_URL=http://127.0.0.1:8010
npm run dev
