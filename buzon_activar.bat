@echo off
setlocal
rem Arranca el buzon y el tunel, y los deja listos para cada inicio de sesion.
cd /d "%~dp0"
call "%~dp0seleccionar_python_bot.bat" 2>nul
if not defined BOT_PYTHON (
    echo [ERROR] No se encontro Python.
    if not "%~1"=="/silencio" pause
    exit /b 1
)
for %%P in ("%BOT_PYTHON%") do set "APP_PYTHONW=%%~dpPpythonw.exe"
if not exist "%APP_PYTHONW%" set "APP_PYTHONW=%BOT_PYTHON%"
set "INICIO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
schtasks /Create /F /SC ONLOGON /TN "MyContent Buzon" /TR "\"%APP_PYTHONW%\" \"%~dp0buzon.py\"" >nul 2>&1
if errorlevel 1 goto carpeta_inicio
schtasks /Create /F /SC ONLOGON /TN "MyContent Tunel" /TR "\"%APP_PYTHONW%\" \"%~dp0buzon_tunel.py\"" >nul 2>&1
if errorlevel 1 goto carpeta_inicio
echo Inicio automatico: Programador de tareas.
goto arrancar
:carpeta_inicio
> "%INICIO%\MyContent_Buzon.vbs" echo Set s = CreateObject("WScript.Shell")
>> "%INICIO%\MyContent_Buzon.vbs" echo s.CurrentDirectory = "%~dp0"
>> "%INICIO%\MyContent_Buzon.vbs" echo s.Run """%APP_PYTHONW%"" ""%~dp0buzon.py""", 0, False
>> "%INICIO%\MyContent_Buzon.vbs" echo s.Run """%APP_PYTHONW%"" ""%~dp0buzon_tunel.py""", 0, False
echo Inicio automatico: carpeta Inicio de Windows (el Programador de tareas pide permisos de administrador).
:arrancar
rem Reactivar no debe dejar dos copias: pisarian la cola y dejarian otro tunel abierto.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'buzon(_tunel)?\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cloudflared.exe' -and $_.CommandLine -match '8787|buzon' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
start "" "%APP_PYTHONW%" "%~dp0buzon.py"
start "" "%APP_PYTHONW%" "%~dp0buzon_tunel.py"
echo Buzon y tunel en marcha. La direccion publica esta en buzon_url.txt
if not "%~1"=="/silencio" pause
