@echo off
chcp 65001 >nul 2>&1
title Content App - Instalar motor de video IA
echo.
echo  ============================================================
echo   Instalador del motor de video IA (torch + diffusers)
echo   Necesita: GPU NVIDIA, drivers actualizados y ~6 GB libres
echo  ============================================================
echo.

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

:: 1. Buscar Python 3.10 - 3.12
set "PY="
py -3.11 --version >nul 2>&1 && set "PY=py -3.11"
if not defined PY py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if not defined PY py -3.10 --version >nul 2>&1 && set "PY=py -3.10"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    echo  [X] No se encontro Python. Instala Python 3.11 desde python.org
    echo      marcando "Add python.exe to PATH" y vuelve a ejecutar este archivo.
    pause
    exit /b 1
)
echo  [OK] Usando: %PY%

:: 2. GPU
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo  [!] No se detecto GPU NVIDIA ^(nvidia-smi^). Sin GPU no se pueden generar videos.
    echo      Actualiza los drivers desde nvidia.com y vuelve a intentarlo.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('nvidia-smi --query-gpu^=name^,memory.total --format^=csv^,noheader') do echo  [OK] GPU: %%i

:: 3. Entorno aislado (no toca el Python del sistema)
if not exist ".venv_video\Scripts\python.exe" (
    echo  [*] Creando entorno .venv_video ...
    %PY% -m venv .venv_video
    if errorlevel 1 (
        echo  [X] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)
set "VPY=%APP_DIR%.venv_video\Scripts\python.exe"

echo  [*] Actualizando pip...
"%VPY%" -m pip install --upgrade pip --quiet

echo  [*] Instalando PyTorch con CUDA 12.6 ^(unos 3 GB, puede tardar^)...
"%VPY%" -m pip install torch --index-url https://download.pytorch.org/whl/cu126
if errorlevel 1 (
    echo  [X] Fallo la instalacion de PyTorch.
    pause
    exit /b 1
)

echo  [*] Instalando diffusers y dependencias...
"%VPY%" -m pip install "diffusers>=0.33" "transformers>=4.48" "accelerate>=1.3" sentencepiece protobuf ftfy pillow imageio-ffmpeg
if errorlevel 1 (
    echo  [X] Fallo la instalacion de diffusers.
    pause
    exit /b 1
)

echo.
echo  [*] Comprobando...
"%VPY%" "%APP_DIR%video_worker.py" --diagnostico
echo.
echo  ============================================================
echo   Listo. Abre Content App, entra al Estudio IA y pulsa
echo   "Comprobar motor". Luego descarga un modelo en el Gestor.
echo  ============================================================
pause
