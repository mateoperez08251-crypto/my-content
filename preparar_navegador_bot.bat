@echo off
setlocal
chcp 65001 >nul
title Preparar navegador del bot
cd /d "%~dp0"
echo Carpeta de la app: "%~dp0"

if not exist "preparar_navegador_bot.py" (
    echo [ERROR] Ejecuta este BAT dentro de la carpeta actualizada de my-content.
    pause
    exit /b 1
)

call :buscar_python
if defined BOT_PYTHON goto instalar
where winget >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Falta Python. Instala Python 3.12 desde python.org y vuelve a ejecutar este BAT.
    pause
    exit /b 1
)
echo Instalando Python 3.12 para tu usuario...
winget install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto error
call :buscar_python
if not defined BOT_PYTHON (
    echo [ERROR] Python instalado, pero aun no se encuentra. Cierra y vuelve a ejecutar este BAT.
    pause
    exit /b 1
)

:instalar
echo Usando "%BOT_PYTHON%"
"%BOT_PYTHON%" "%~dp0preparar_navegador_bot.py"
if errorlevel 1 goto error
echo.
echo Listo. En el bot pulsa Sesion para entrar a tu cuenta.
pause
exit /b 0

:buscar_python
call "%~dp0seleccionar_python_bot.bat"
exit /b 0

:error
echo.
echo [ERROR] No se completo la preparacion. Revisa el mensaje anterior; no se anuncia exito.
pause
exit /b 1
