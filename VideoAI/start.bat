@echo off
chcp 65001 >nul 2>&1
title VideoAI - Servidor Activo
color 0A

set "VIDEOAI_DIR=%~dp0"
cd /d "%VIDEOAI_DIR%"

:: Agregar ffmpeg local al PATH
set "PATH=%VIDEOAI_DIR%;%PATH%"

call "%VIDEOAI_DIR%.venv\Scripts\activate.bat"

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║     VideoAI - Iniciando Servidor                 ║
echo  ║     http://127.0.0.1:7860                        ║
echo  ╚══════════════════════════════════════════════════╝
echo.
echo  Abriendo navegador...
start http://127.0.0.1:7860

python video_ai_server.py
pause
