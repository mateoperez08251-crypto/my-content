@echo off
rem Se llama desde los BAT del proyecto; deja BOT_PYTHON en el entorno llamante.
set "BOT_PYTHON="
if defined CONTENTAPP_PYTHON call :probar_python "%CONTENTAPP_PYTHON%"
if defined BOT_PYTHON exit /b 0
call :probar_python "%~dp0.venv\Scripts\python.exe"
if defined BOT_PYTHON exit /b 0
call :probar_python "%~dp0venv\Scripts\python.exe"
if defined BOT_PYTHON exit /b 0
call :probar_python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined BOT_PYTHON exit /b 0
for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do if not defined BOT_PYTHON call :probar_python "%%P"
if defined BOT_PYTHON exit /b 0
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do if not defined BOT_PYTHON call :probar_python "%%P"
if defined BOT_PYTHON exit /b 0
for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do if not defined BOT_PYTHON call :probar_python "%%P"
if defined BOT_PYTHON exit /b 0
exit /b 1

:probar_python
if not exist "%~1" exit /b 1
rem Un python.exe de venv puede existir aunque se haya borrado su Python base.
rem Comprobar el arranque antes de seleccionarlo, sin borrar ni activar el entorno.
"%~1" -c "import sys; sys.exit(0 if sys.version_info.major == 3 else 1)" >nul 2>nul
if not "%errorlevel%"=="0" (
    echo [AVISO] Python no arranca; se omite "%~1".
    exit /b 1
)
set "BOT_PYTHON=%~1"
exit /b 0
