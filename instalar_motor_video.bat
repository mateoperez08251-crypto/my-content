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

:: --- PyTorch: descarga reanudable con reintentos (el archivo pesa ~2.5 GB) ---
set "WHEELS=%APP_DIR%.venv_video\descargas"
if not exist "%WHEELS%" mkdir "%WHEELS%"
set "REPORTE=%WHEELS%\torch_reporte.json"
set "URLTXT=%WHEELS%\torch_url.txt"

"%VPY%" -c "import torch" >nul 2>&1
if not errorlevel 1 (
    echo  [OK] PyTorch ya estaba instalado.
    goto diffusers
)

echo  [*] Buscando la version de PyTorch para tu Python...
"%VPY%" -m pip install torch --index-url https://download.pytorch.org/whl/cu126 --dry-run --no-deps --ignore-installed --report "%REPORTE%" --quiet --retries 10 --timeout 120
if errorlevel 1 (
    echo  [X] No se pudo consultar el servidor de PyTorch. Revisa tu internet y vuelve a ejecutar.
    pause
    exit /b 1
)
"%VPY%" -c "import json,urllib.parse as u;d=json.load(open(r'%REPORTE%'))['install'][0]['download_info']['url'].split('#')[0];print(d);print(u.unquote(d.rsplit('/',1)[1]))" > "%URLTXT%"
set /p TORCH_URL=<"%URLTXT%"
for /f "usebackq skip=1 delims=" %%n in ("%URLTXT%") do set "TORCH_WHL=%%n"
set "TORCH_FILE=%WHEELS%\%TORCH_WHL%"
echo  [*] Archivo: %TORCH_WHL%

set INTENTO=0
:descarga_torch
if exist "%TORCH_FILE%.ok" goto instalar_torch
set /a INTENTO+=1
echo  [*] Descargando PyTorch (intento %INTENTO% de 8). Si se corta, sigue donde iba...
curl.exe -L -C - --retry 10 --retry-delay 5 --connect-timeout 30 -o "%TORCH_FILE%" "%TORCH_URL%"
if not errorlevel 1 (
    echo ok> "%TORCH_FILE%.ok"
    goto instalar_torch
)
if %INTENTO% geq 8 (
    echo  [X] La descarga de PyTorch fallo varias veces. Revisa tu conexion y vuelve a
    echo      ejecutar este archivo: continuara donde se quedo.
    pause
    exit /b 1
)
timeout /t 10 /nobreak >nul
goto descarga_torch

:instalar_torch
set INTENTO=0
:reintento_instalar_torch
set /a INTENTO+=1
echo  [*] Instalando PyTorch desde el archivo descargado (intento %INTENTO% de 3)...
"%VPY%" -m pip install "%TORCH_FILE%" --retries 10 --timeout 120
if not errorlevel 1 goto diffusers
if %INTENTO% geq 3 (
    echo  [X] No se pudo instalar PyTorch. Si el error dice "WinError 32", pausa el antivirus
    echo      un momento o agrega esta carpeta como exclusion, y vuelve a ejecutar.
    echo      Si dice que el archivo esta dañado, borra la carpeta .venv_video\descargas.
    pause
    exit /b 1
)
echo  [!] Fallo; el antivirus puede estar revisando el archivo. Reintentando en 15 s...
timeout /t 15 /nobreak >nul
goto reintento_instalar_torch

:diffusers
set INTENTO=0
:reintento_diffusers
set /a INTENTO+=1
echo  [*] Instalando diffusers y dependencias (intento %INTENTO% de 3)...
"%VPY%" -m pip install "diffusers>=0.33" "transformers>=4.48" "accelerate>=1.3" sentencepiece protobuf ftfy pillow imageio-ffmpeg --retries 10 --timeout 120
if not errorlevel 1 goto comprobar
if %INTENTO% geq 3 (
    echo  [X] Fallo la instalacion de diffusers. Revisa tu internet y vuelve a ejecutar.
    pause
    exit /b 1
)
timeout /t 15 /nobreak >nul
goto reintento_diffusers

:comprobar
:: --- Demucs (para "Separar Musica"). Opcional: si falla, el video sigue funcionando ---
echo  [*] Instalando Demucs para separar musica (opcional)...
"%VPY%" -c "import torch;print(torch.__version__.split('+')[0])" > "%WHEELS%\torch_version.txt"
set /p TORCH_VER=<"%WHEELS%\torch_version.txt"
"%VPY%" -m pip install torchaudio "torch==%TORCH_VER%" --index-url https://download.pytorch.org/whl/cu126 --retries 10 --timeout 120
"%VPY%" -m pip install demucs soundfile "torch==%TORCH_VER%" --extra-index-url https://download.pytorch.org/whl/cu126 --retries 10 --timeout 120
if errorlevel 1 echo  [!] Demucs no se pudo instalar: "Separar Musica" no funcionara. El video si.

echo.
echo  [*] Comprobando...
"%VPY%" "%APP_DIR%video_worker.py" --diagnostico
echo.
echo  ============================================================
echo   Listo. Abre Content App, entra al Estudio IA y pulsa
echo   "Comprobar motor". Luego descarga un modelo en el Gestor.
echo  ============================================================
pause
