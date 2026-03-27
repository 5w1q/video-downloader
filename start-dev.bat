@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"

echo Starting backend (port 8000) ...
start "free-video-downloader-backend" powershell -NoExit -Command "Set-Location -LiteralPath '%ROOT%backend'; python main.py"

timeout /t 2 /nobreak >nul

echo Starting frontend (Vite) ...
start "free-video-downloader-frontend" powershell -NoExit -Command "Set-Location -LiteralPath '%ROOT%frontend'; npm run dev"

echo.
echo Two windows opened. Close them to stop servers.
echo Frontend URL is usually http://localhost:5173
endlocal
