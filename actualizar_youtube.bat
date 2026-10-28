@echo off
setlocal
chcp 65001 >nul
title Actualizar soporte de YouTube
cd /d "%~dp0"
echo Cierra My Content antes de continuar. Luego vuelve a abrirlo para aplicar la actualizacion.
pause
call "%~dp0seleccionar_python_bot.bat"
if not defined BOT_PYTHON (
    echo [ERROR] No se encontro un Python que funcione. Ejecuta preparar_navegador_bot.bat.
    pause
    exit /b 1
)
echo Usando "%BOT_PYTHON%"
"%BOT_PYTHON%" -m pip install --upgrade "yt-dlp[default,deno]"
if errorlevel 1 goto error
"%BOT_PYTHON%" -c "import subprocess, yt_dlp, yt_dlp_ejs; from importlib.metadata import version; from deno import find_deno_bin; print('yt-dlp:', version('yt-dlp')); print('EJS:', version('yt-dlp-ejs')); subprocess.run([find_deno_bin(), '--version'], check=True)"
if errorlevel 1 goto error
echo Listo. Abre My Content y vuelve a iniciar la captura del live.
pause
exit /b 0

:error
echo [ERROR] No se completo la actualizacion. Revisa el mensaje anterior.
pause
exit /b 1
