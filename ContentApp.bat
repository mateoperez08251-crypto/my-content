@echo off
setlocal
cd /d "%~dp0"
call "%~dp0seleccionar_python_bot.bat"
if not defined BOT_PYTHON (
    echo [ERROR] No se encontro un Python que funcione. Ejecuta preparar_navegador_bot.bat.
    pause
    exit /b 1
)
for %%P in ("%BOT_PYTHON%") do set "APP_PYTHONW=%%~dpPpythonw.exe"
if exist "%APP_PYTHONW%" (
    start "" "%APP_PYTHONW%" "%~dp0content.py"
) else (
    start "" "%BOT_PYTHON%" "%~dp0content.py"
)
