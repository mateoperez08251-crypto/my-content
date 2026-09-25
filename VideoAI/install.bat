@echo off
chcp 65001 >nul 2>&1
title VideoAI - Instalador Completo (RTX A5000)
color 0A

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║     VideoAI - Instalador Automatico              ║
echo  ║     Optimizado para RTX A5000 (24GB VRAM)        ║
echo  ╚══════════════════════════════════════════════════╝
echo.

set "VIDEOAI_DIR=%~dp0"
cd /d "%VIDEOAI_DIR%"

:: ============================================================
:: PASO 1: Verificar Python
:: ============================================================
echo [1/6] Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [!] Python NO esta instalado.
    echo  [!] Descargando Python 3.11.9...
    echo.
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%TEMP%\python_installer.exe'"
    echo  [*] Instalando Python 3.11.9 (esto tarda 1-2 minutos)...
    "%TEMP%\python_installer.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1
    del "%TEMP%\python_installer.exe" >nul 2>&1
    
    :: Refrescar PATH
    set "PATH=C:\Program Files\Python311;C:\Program Files\Python311\Scripts;%PATH%"
    
    python --version >nul 2>&1
    if errorlevel 1 (
        echo  [X] ERROR: No se pudo instalar Python. Instalalo manualmente desde python.org
        pause
        exit /b 1
    )
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do echo  [OK] %%i detectado

:: ============================================================
:: PASO 2: Verificar CUDA
:: ============================================================
echo.
echo [2/6] Verificando CUDA Toolkit...
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo  [!] nvidia-smi no encontrado. Asegurate de tener los drivers NVIDIA instalados.
    echo  [!] Descarga CUDA Toolkit 12.6 desde: https://developer.nvidia.com/cuda-downloads
    echo.
    echo  Presiona cualquier tecla para continuar de todos modos...
    pause >nul
) else (
    for /f "tokens=*" %%i in ('nvidia-smi --query-gpu=name --format=csv,noheader 2^>^&1') do (
        echo  [OK] GPU detectada: %%i
    )
    for /f "tokens=*" %%i in ('nvidia-smi --query-gpu=memory.total --format=csv,noheader 2^>^&1') do (
        echo  [OK] VRAM: %%i
    )
)

:: ============================================================
:: PASO 3: Verificar FFmpeg
:: ============================================================
echo.
echo [3/6] Verificando FFmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo  [!] FFmpeg NO esta instalado. Descargando...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile '%TEMP%\ffmpeg.zip'"
    echo  [*] Extrayendo FFmpeg...
    powershell -Command "Expand-Archive -Path '%TEMP%\ffmpeg.zip' -DestinationPath '%VIDEOAI_DIR%' -Force"
    
    :: Mover ffmpeg al directorio raiz
    for /d %%d in ("%VIDEOAI_DIR%ffmpeg-*") do (
        if exist "%%d\bin\ffmpeg.exe" (
            copy "%%d\bin\ffmpeg.exe" "%VIDEOAI_DIR%" >nul
            copy "%%d\bin\ffprobe.exe" "%VIDEOAI_DIR%" >nul
            rmdir /s /q "%%d" >nul 2>&1
        )
    )
    del "%TEMP%\ffmpeg.zip" >nul 2>&1
    
    if exist "%VIDEOAI_DIR%ffmpeg.exe" (
        echo  [OK] FFmpeg instalado correctamente
    ) else (
        echo  [!] No se pudo instalar FFmpeg automaticamente
    )
) else (
    echo  [OK] FFmpeg ya esta instalado
)

:: ============================================================
:: PASO 4: Verificar Git
:: ============================================================
echo.
echo [4/6] Verificando Git...
git --version >nul 2>&1
if errorlevel 1 (
    echo  [!] Git NO esta instalado. Descargando...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.2/Git-2.47.1.2-64-bit.exe' -OutFile '%TEMP%\git_installer.exe'"
    echo  [*] Instalando Git...
    "%TEMP%\git_installer.exe" /VERYSILENT /NORESTART
    del "%TEMP%\git_installer.exe" >nul 2>&1
    set "PATH=C:\Program Files\Git\bin;%PATH%"
    echo  [OK] Git instalado
) else (
    echo  [OK] Git ya esta instalado
)

:: ============================================================
:: PASO 5: Crear entorno virtual e instalar dependencias
:: ============================================================
echo.
echo [5/6] Creando entorno virtual Python...

if not exist "%VIDEOAI_DIR%.venv" (
    python -m venv "%VIDEOAI_DIR%.venv"
    echo  [OK] Entorno virtual creado
) else (
    echo  [OK] Entorno virtual ya existe
)

call "%VIDEOAI_DIR%.venv\Scripts\activate.bat"

echo.
echo  [*] Instalando PyTorch con soporte CUDA 12.6...
echo  [*] (Esto puede tardar varios minutos la primera vez)
echo.
pip install --upgrade pip >nul 2>&1
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126

echo.
echo  [*] Instalando dependencias de IA...
pip install -r "%VIDEOAI_DIR%requirements.txt" --extra-index-url https://download.pytorch.org/whl/cu126

echo.
echo  [*] Instalando SAM 2 (Meta - Segmentacion de video)...
pip install "git+https://github.com/facebookresearch/sam2.git"

:: ============================================================
:: PASO 6: Crear carpetas necesarias
:: ============================================================
echo.
echo [6/6] Creando estructura de carpetas...
if not exist "%VIDEOAI_DIR%models" mkdir "%VIDEOAI_DIR%models"
if not exist "%VIDEOAI_DIR%models\hunyuan" mkdir "%VIDEOAI_DIR%models\hunyuan"
if not exist "%VIDEOAI_DIR%models\realesrgan" mkdir "%VIDEOAI_DIR%models\realesrgan"
if not exist "%VIDEOAI_DIR%models\rife" mkdir "%VIDEOAI_DIR%models\rife"
if not exist "%VIDEOAI_DIR%models\sam2" mkdir "%VIDEOAI_DIR%models\sam2"
if not exist "%VIDEOAI_DIR%models\sadtalker" mkdir "%VIDEOAI_DIR%models\sadtalker"
if not exist "%VIDEOAI_DIR%outputs" mkdir "%VIDEOAI_DIR%outputs"
if not exist "%VIDEOAI_DIR%temp" mkdir "%VIDEOAI_DIR%temp"
if not exist "%VIDEOAI_DIR%uploads" mkdir "%VIDEOAI_DIR%uploads"
echo  [OK] Carpetas creadas

:: ============================================================
:: Verificacion final
:: ============================================================
echo.
echo  ============================================
echo   Verificacion de GPU con PyTorch:
echo  ============================================
python -c "import torch; print(f'  PyTorch: {torch.__version__}'); print(f'  CUDA disponible: {torch.cuda.is_available()}'); print(f'  GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"NO DETECTADA\"}'); print(f'  VRAM: {torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f} GB' if torch.cuda.is_available() else '')"

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║  INSTALACION COMPLETADA                          ║
echo  ║                                                  ║
echo  ║  Siguiente paso:                                 ║
echo  ║  1. Ejecuta  download_models.bat                 ║
echo  ║  2. Luego ejecuta  start.bat                     ║
echo  ╚══════════════════════════════════════════════════╝
echo.
pause
