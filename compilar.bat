@echo off
chcp 65001 >nul
title Compilar ContentAppPro - Ejecutable
color 0A

echo.
echo â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—
echo â•‘       COMPILADOR DE ContentAppPro v2.0              â•‘
echo â•‘       Incluye: Estudio IA + Editor + Clonador       â•‘
echo â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
echo.

:: ============================================
:: 1. Verificar que estamos en la carpeta correcta
:: ============================================
if not exist "content.py" (
    echo [ERROR] No se encontrÃ³ content.py en esta carpeta.
    echo         Ejecuta este script desde "D:\my content"
    timeout /t 5 >nul
    exit /b 1
)

if not exist "modulo_ia.py" (
    echo [ERROR] No se encontrÃ³ modulo_ia.py
    timeout /t 5 >nul
    exit /b 1
)

:: ============================================
:: 2. Activar entorno virtual si existe
:: ============================================
:: No activamos .venv localmente porque parece estar corrupto. Usamos Python global.

:: ============================================
:: 3. Instalar/Actualizar PyInstaller
:: ============================================
echo.
echo [1/5] Instalando PyInstaller...
py -m pip install pyinstaller --quiet --upgrade
if %errorlevel% neq 0 (
    echo [ERROR] FallÃ³ la instalaciÃ³n de PyInstaller
    timeout /t 5 >nul
    exit /b 1
)
echo       OK

:: ============================================
:: 4. Instalar dependencias del proyecto
:: ============================================
echo [2/5] Instalando dependencias del proyecto...
py -m pip install flask firebase-admin pyautogui pynput send2trash requests pyperclip imageio imageio-ffmpeg pillow opencv-python mediapipe pystray --quiet
echo       OK

:: ============================================
:: 5. Limpiar builds anteriores
:: ============================================
echo [3/5] Limpiando builds anteriores...
if exist "build\ContentAppPro" rmdir /s /q "build\ContentAppPro"
if exist "dist\ContentAppPro" rmdir /s /q "dist\ContentAppPro"
echo       OK

:: ============================================
:: 6. Compilar con PyInstaller
:: ============================================
echo [4/5] Compilando ejecutable (esto tarda 2-5 minutos)...
echo.
py -m PyInstaller ContentAppPro.spec --clean --noconfirm
if %errorlevel% neq 0 (
    echo.
    echo â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—
    echo â•‘  [ERROR] La compilaciÃ³n fallÃ³.                      â•‘
    echo â•‘  Revisa los errores arriba.                         â•‘
    echo â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    timeout /t 5 >nul
    exit /b 1
)

:: ============================================
:: 7. Copiar archivos extras que no empaqueta PyInstaller
:: ============================================
echo.
echo [5/5] Copiando archivos adicionales...

set DIST=dist\ContentAppPro

:: MÃ³dulos Python locales
copy /y "modulo_ia.py" "%DIST%\" >nul 2>&1
copy /y "editor.py" "%DIST%\" >nul 2>&1
copy /y "api_subidor.py" "%DIST%\" >nul 2>&1
copy /y "api_clonador_flask.py" "%DIST%\" >nul 2>&1
copy /y "smart_editor.py" "%DIST%\" >nul 2>&1
copy /y "audio_extractor.py" "%DIST%\" >nul 2>&1
copy /y "audio_separator.py" "%DIST%\" >nul 2>&1
copy /y "yt_downloader.py" "%DIST%\" >nul 2>&1
copy /y "youtube_uploader.py" "%DIST%\" >nul 2>&1
copy /y "descargar_modelo.py" "%DIST%\" >nul 2>&1
copy /y "generate_prompt_variation.py" "%DIST%\" >nul 2>&1
copy /y "video_worker.py" "%DIST%\" >nul 2>&1
copy /y "transcripcion_local.py" "%DIST%\" >nul 2>&1
copy /y "tts_worker.py" "%DIST%\" >nul 2>&1
copy /y "clips_virales.py" "%DIST%\" >nul 2>&1
copy /y "reencuadre.py" "%DIST%\" >nul 2>&1
copy /y "gpu_video.py" "%DIST%\" >nul 2>&1
copy /y "instalar_motor_video.bat" "%DIST%\" >nul 2>&1

:: ConfiguraciÃ³n y datos
copy /y "firebase-key.json" "%DIST%\" >nul 2>&1
copy /y "firebase.json" "%DIST%\" >nul 2>&1
copy /y "client_secrets.json" "%DIST%\" >nul 2>&1
copy /y "secrets.json" "%DIST%\" >nul 2>&1
copy /y "config.json" "%DIST%\" >nul 2>&1
copy /y "requirements.txt" "%DIST%\" >nul 2>&1
copy /y "dataset_director.jsonl" "%DIST%\" >nul 2>&1
copy /y "styles_pool.json" "%DIST%\" >nul 2>&1
copy /y "logo_b64.txt" "%DIST%\" >nul 2>&1
copy /y "dummy.wav" "%DIST%\" >nul 2>&1
copy /y "instalar_dependencias.bat" "%DIST%\" >nul 2>&1
copy /y "instalar_git.bat" "%DIST%\" >nul 2>&1

:: ffmpeg y haarcascade
copy /y "ffmpeg.exe" "%DIST%\" >nul 2>&1
copy /y "haarcascade_frontalface_default.xml" "%DIST%\" >nul 2>&1

:: Carpetas auxiliares
if exist "Clonar-voz" xcopy /e /i /y "Clonar-voz" "%DIST%\Clonar-voz" >nul 2>&1
if exist "chrome_tiktok" xcopy /e /i /y "chrome_tiktok" "%DIST%\chrome_tiktok" >nul 2>&1
if exist "VideoAI" xcopy /e /i /y "VideoAI" "%DIST%\VideoAI" >nul 2>&1
echo \venv\> exclude.txt
echo \__pycache__\>> exclude.txt
if exist "firebase_radar" xcopy /e /i /y /exclude:exclude.txt "firebase_radar" "%DIST%\firebase_radar" >nul 2>&1
del exclude.txt

:: Crear carpetas vacÃ­as necesarias en runtime
if not exist "%DIST%\videos_procesados" mkdir "%DIST%\videos_procesados"
if not exist "%DIST%\videos_descargados" mkdir "%DIST%\videos_descargados"
if not exist "%DIST%\temp" mkdir "%DIST%\temp"
if not exist "%DIST%\downloads" mkdir "%DIST%\downloads"
if not exist "%DIST%\downloads\audio" mkdir "%DIST%\downloads\audio"
if not exist "%DIST%\models" mkdir "%DIST%\models"
if not exist "%DIST%\models\video_ai" mkdir "%DIST%\models\video_ai"
if not exist "%DIST%\voces" mkdir "%DIST%\voces"
if not exist "%DIST%\salidas" mkdir "%DIST%\salidas"
if not exist "%DIST%\uploads" mkdir "%DIST%\uploads"
if not exist "%DIST%\assets_subidos" mkdir "%DIST%\assets_subidos"

echo       OK

:: ============================================
:: 8. Verificar resultado
:: ============================================
echo.
if exist "%DIST%\ContentAppPro.exe" (
    for %%A in ("%DIST%\ContentAppPro.exe") do set EXE_SIZE=%%~zA
    echo â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—
    echo â•‘  COMPILACIÃ“N EXITOSA                                â•‘
    echo â•‘                                                     â•‘
    echo â•‘  Ejecutable: dist\ContentAppPro\ContentAppPro.exe   â•‘
    echo â•‘                                                     â•‘
    echo â•‘  Para crear el instalador, ejecuta Inno Setup con:  â•‘
    echo â•‘  setup_script.iss                                   â•‘
    echo â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
) else (
    echo â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—
    echo â•‘  [ERROR] No se generÃ³ el ejecutable.                â•‘
    echo â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
)

echo.
timeout /t 5 >nul
