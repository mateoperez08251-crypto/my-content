@echo off
chcp 65001 >nul 2>&1
title VideoAI - Descarga de Modelos IA
color 0B

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║     VideoAI - Descarga de Modelos IA             ║
echo  ║     RTX A5000 (24GB VRAM)                        ║
echo  ╚══════════════════════════════════════════════════╝
echo.
echo  ADVERTENCIA: Esto descargara ~25-30 GB de modelos.
echo  Asegurate de tener espacio en disco y buena conexion.
echo.
pause

set "VIDEOAI_DIR=%~dp0"
cd /d "%VIDEOAI_DIR%"
call "%VIDEOAI_DIR%.venv\Scripts\activate.bat"

:: ============================================================
:: 1. HunyuanVideo (Text-to-Video) - El mas potente
:: ============================================================
echo.
echo  ============================================
echo   [1/5] Descargando HunyuanVideo (T2V)
echo   Tamaño: ~15 GB
echo  ============================================
echo.

python -c "
from huggingface_hub import snapshot_download
import os

model_dir = os.path.join(r'%VIDEOAI_DIR%', 'models', 'hunyuan')
print('  Descargando HunyuanVideo desde HuggingFace...')
print('  (Esto puede tardar 30-60 minutos dependiendo de tu conexion)')
print()

try:
    snapshot_download(
        repo_id='tencent/HunyuanVideo',
        local_dir=model_dir,
        local_dir_use_symlinks=False,
        resume_download=True,
        ignore_patterns=['*.md', '*.txt', '.gitattributes'],
    )
    print('  [OK] HunyuanVideo descargado correctamente')
except Exception as e:
    print(f'  [!] Error descargando HunyuanVideo: {e}')
    print('  [!] Puedes reintentar ejecutando este script de nuevo.')
"

:: ============================================================
:: 2. Real-ESRGAN (Upscaling 4K)
:: ============================================================
echo.
echo  ============================================
echo   [2/5] Descargando Real-ESRGAN (Upscaling)
echo   Tamaño: ~67 MB
echo  ============================================
echo.

powershell -Command "Invoke-WebRequest -Uri 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth' -OutFile '%VIDEOAI_DIR%models\realesrgan\realesr-general-x4v3.pth'"
if exist "%VIDEOAI_DIR%models\realesrgan\realesr-general-x4v3.pth" (
    echo  [OK] Real-ESRGAN x4 descargado
) else (
    echo  [!] Error descargando Real-ESRGAN
)

powershell -Command "Invoke-WebRequest -Uri 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth' -OutFile '%VIDEOAI_DIR%models\realesrgan\RealESRGAN_x4plus.pth'"
if exist "%VIDEOAI_DIR%models\realesrgan\RealESRGAN_x4plus.pth" (
    echo  [OK] Real-ESRGAN x4plus descargado
) else (
    echo  [!] Error descargando Real-ESRGAN x4plus
)

:: ============================================================
:: 3. RIFE (Interpolacion de frames / Camara lenta)
:: ============================================================
echo.
echo  ============================================
echo   [3/5] Descargando RIFE (Interpolacion)
echo   Tamaño: ~150 MB
echo  ============================================
echo.

if not exist "%VIDEOAI_DIR%models\rife\flownet.pkl" (
    python -c "
from huggingface_hub import hf_hub_download
import os

model_dir = os.path.join(r'%VIDEOAI_DIR%', 'models', 'rife')
print('  Descargando modelo RIFE 4.22...')
try:
    for f in ['flownet.pkl', 'contextnet.pkl', 'unet.pkl']:
        hf_hub_download(
            repo_id='AlexWortworworworworworw/RIFE',
            filename=f,
            local_dir=model_dir,
            local_dir_use_symlinks=False,
        )
    print('  [OK] RIFE descargado')
except Exception as e:
    print(f'  [!] Error: {e}')
    print('  [!] Se descargara automaticamente al usar la app.')
"
) else (
    echo  [OK] RIFE ya esta descargado
)

:: ============================================================
:: 4. SAM 2 (Segmentacion / Eliminacion de fondo)
:: ============================================================
echo.
echo  ============================================
echo   [4/5] Descargando SAM 2 (Segmentacion)
echo   Tamaño: ~2.4 GB
echo  ============================================
echo.

python -c "
from huggingface_hub import hf_hub_download
import os

model_dir = os.path.join(r'%VIDEOAI_DIR%', 'models', 'sam2')
print('  Descargando SAM 2.1 Large...')
try:
    hf_hub_download(
        repo_id='facebook/sam2.1-hiera-large',
        filename='sam2.1_hiera_large.pt',
        local_dir=model_dir,
        local_dir_use_symlinks=False,
    )
    print('  [OK] SAM 2 descargado')
except Exception as e:
    print(f'  [!] Error: {e}')
"

:: ============================================================
:: 5. SadTalker (Lip-sync / Animacion facial)
:: ============================================================
echo.
echo  ============================================
echo   [5/5] Descargando SadTalker (Lip-Sync)
echo   Tamaño: ~1.5 GB
echo  ============================================
echo.

python -c "
from huggingface_hub import snapshot_download
import os

model_dir = os.path.join(r'%VIDEOAI_DIR%', 'models', 'sadtalker')
print('  Descargando modelos SadTalker...')
try:
    snapshot_download(
        repo_id='vinthony/SadTalker-V002',
        local_dir=model_dir,
        local_dir_use_symlinks=False,
        resume_download=True,
    )
    print('  [OK] SadTalker descargado')
except Exception as e:
    print(f'  [!] Error: {e}')
"

:: ============================================================
:: Resumen
:: ============================================================
echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║  DESCARGA DE MODELOS COMPLETADA                  ║
echo  ║                                                  ║
echo  ║  Modelos instalados:                             ║
echo  ║  - HunyuanVideo  (Text/Image to Video)          ║
echo  ║  - Real-ESRGAN    (Upscaling 4K)                ║
echo  ║  - RIFE 4.22      (Camara lenta IA)             ║
echo  ║  - SAM 2          (Segmentacion)                ║
echo  ║  - SadTalker      (Lip-Sync)                    ║
echo  ║                                                  ║
echo  ║  Ahora ejecuta:  start.bat                      ║
echo  ╚══════════════════════════════════════════════════╝
echo.
pause
