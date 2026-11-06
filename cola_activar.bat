@echo off
setlocal
rem Revisa cola.json cada 5 minutos con el Programador de tareas de Windows.
cd /d "%~dp0"
call "%~dp0seleccionar_python_bot.bat"
if not defined BOT_PYTHON (
    echo [ERROR] No se encontro Python. Ejecuta preparar_navegador_bot.bat.
    pause
    exit /b 1
)
for %%P in ("%BOT_PYTHON%") do set "APP_PYTHONW=%%~dpPpythonw.exe"
if not exist "%APP_PYTHONW%" set "APP_PYTHONW=%BOT_PYTHON%"
schtasks /Create /F /SC MINUTE /MO 5 /TN "MyContent Cola" /TR "\"%APP_PYTHONW%\" \"%~dp0cola_subida.py\""
if errorlevel 1 (
    echo [ERROR] No se pudo crear la tarea.
) else (
    echo Cola activada: revisa cola.json cada 5 minutos.
)
if not "%~1"=="/silencio" pause
