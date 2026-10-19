@echo off
rem Se llama desde los BAT del proyecto; deja BOT_PYTHON en el entorno llamante.
set "BOT_PYTHON="
if defined CONTENTAPP_PYTHON if exist "%CONTENTAPP_PYTHON%" set "BOT_PYTHON=%CONTENTAPP_PYTHON%"
if defined BOT_PYTHON exit /b 0
if exist "%~dp0.venv\Scripts\python.exe" set "BOT_PYTHON=%~dp0.venv\Scripts\python.exe"
if defined BOT_PYTHON exit /b 0
if exist "%~dp0venv\Scripts\python.exe" set "BOT_PYTHON=%~dp0venv\Scripts\python.exe"
if defined BOT_PYTHON exit /b 0
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "BOT_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined BOT_PYTHON exit /b 0
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "BOT_PYTHON=%%P"
if defined BOT_PYTHON exit /b 0
for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "BOT_PYTHON=%%P"
exit /b 0
