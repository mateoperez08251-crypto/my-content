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

:: El motor se instala FUERA del proyecto, en el MISMO DISCO que la app
:: (p. ej. D:\ContentApp\motor_video): así no llena el disco C y VS Code u otros
:: programas no bloquean sus archivos. Se puede cambiar con la variable
:: CONTENTAPP_MOTOR_DIR.
set "MOTOR_BASE=%~d0\ContentApp"
if defined CONTENTAPP_MOTOR_DIR (set "VENV=%CONTENTAPP_MOTOR_DIR%") else (set "VENV=%MOTOR_BASE%\motor_video")
if not exist "%MOTOR_BASE%" mkdir "%MOTOR_BASE%"

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
tasklist /fi "imagename eq ContentAppPro.exe" 2>nul | find /i "ContentAppPro.exe" >nul
if not errorlevel 1 (
    echo  [!] Content App esta abierta. Cierrala ^(tambien desde la bandeja junto al reloj^)
    echo      y pulsa una tecla para seguir.
    pause >nul
)
call :liberar_venv
if exist ".venv_video" (
    echo  [i] La carpeta vieja .venv_video ya no se usa. Si no te deja borrarla, reinicia el PC y borrala.
)
:: No usar paquetes del Python del usuario (evita que un torch viejo "se cuele")
set "PYTHONNOUSERSITE=1"
set "VPY=%VENV%\Scripts\python.exe"
echo  [*] Carpeta del motor: %VENV%

if exist "%VPY%" goto validar_entorno
if exist "%VENV%" goto rehacer_entorno
goto crear_entorno

:validar_entorno
"%VPY%" -m pip --version >nul 2>&1
if errorlevel 1 goto rehacer_entorno
"%VPY%" -c "import os,sys,pip._vendor.certifi as c;sys.exit(0 if os.path.exists(c.where()) else 1)" >nul 2>&1
if errorlevel 1 goto rehacer_entorno
echo  [OK] Entorno del motor correcto.
goto entorno_ok

:rehacer_entorno
echo  [!] El entorno del motor esta incompleto o dañado: se borra y se crea de nuevo.
echo      Si falla, cierra Content App (tambien desde la bandeja junto al reloj).
:: se conserva lo ya descargado de PyTorch para no bajarlo otra vez
if exist "%VENV%\descargas" (
    if exist "%MOTOR_BASE%\_descargas_motor" rmdir /s /q "%MOTOR_BASE%\_descargas_motor" >nul 2>&1
    move "%VENV%\descargas" "%MOTOR_BASE%\_descargas_motor" >nul 2>&1
)
call :liberar_venv
rmdir /s /q "%VENV%" >nul 2>&1
if exist "%VENV%" (
    echo  [X] No se pudo borrar %VENV% porque algun programa lo esta usando.
    echo      Cierra Content App, espera unos segundos y vuelve a ejecutar este archivo.
    pause
    exit /b 1
)

:crear_entorno
echo  [*] Creando entorno del motor ...
%PY% -m venv "%VENV%"
if errorlevel 1 (
    echo  [X] No se pudo crear el entorno virtual.
    pause
    exit /b 1
)
if exist "%MOTOR_BASE%\_descargas_motor" move "%MOTOR_BASE%\_descargas_motor" "%VENV%\descargas" >nul 2>&1

:entorno_ok
echo  [*] Actualizando pip...
"%VPY%" -m pip install --upgrade pip --quiet --retries 10 --timeout 120
if errorlevel 1 "%VPY%" -m ensurepip --upgrade >nul 2>&1

:: --- PyTorch: descarga reanudable con reintentos (el archivo pesa ~2.5 GB) ---
set "WHEELS=%VENV%\descargas"
if not exist "%WHEELS%" mkdir "%WHEELS%"
:: reaprovechar PyTorch ya descargado en la carpeta vieja (.venv_video)
if exist "%APP_DIR%.venv_video\descargas\*.whl" (
    echo  [*] Reutilizando la descarga de PyTorch de la carpeta vieja...
    copy /y "%APP_DIR%.venv_video\descargas\*.whl" "%WHEELS%\" >nul 2>&1
    copy /y "%APP_DIR%.venv_video\descargas\*.ok" "%WHEELS%\" >nul 2>&1
)
set "REPORTE=%WHEELS%\torch_reporte.json"
set "URLTXT=%WHEELS%\torch_url.txt"

:: PyTorch ya instalado: solo se acepta si es < 2.15 y funciona de verdad en la GPU
"%VPY%" -c "import torch;v=tuple(int(x) for x in torch.__version__.split('+')[0].split('.')[:2]);assert v<(2,15);assert torch.cuda.is_available();(torch.ones(8,device='cuda')*2).sum().item()" >nul 2>&1
if not errorlevel 1 (
    echo  [OK] PyTorch ya estaba instalado y funciona con tu GPU.
    goto diffusers
)
"%VPY%" -c "import torch" >nul 2>&1
if not errorlevel 1 (
    echo  [!] El PyTorch instalado no sirve para tu GPU: se reinstala.
    call :liberar_venv
    "%VPY%" -m pip uninstall -y torch torchvision torchaudio >nul 2>&1
)

echo  [*] Buscando la version de PyTorch para tu Python...
:: torch < 2.15: las versiones siguientes quitan el soporte de las GPU Pascal (GTX 10xx, TITAN Xp)
"%VPY%" -m pip install "torch<2.15" --index-url https://download.pytorch.org/whl/cu126 --dry-run --no-deps --ignore-installed --report "%REPORTE%" --quiet --retries 10 --timeout 120
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
call :liberar_venv
if %INTENTO% gtr 1 if exist "%VENV%\Lib\site-packages\torch" rmdir /s /q "%VENV%\Lib\site-packages\torch" >nul 2>&1
echo  [*] Instalando PyTorch desde el archivo descargado (intento %INTENTO% de 3)...
"%VPY%" -m pip install "%TORCH_FILE%" --retries 10 --timeout 120
if not errorlevel 1 goto diffusers
if %INTENTO% geq 3 (
    echo  [X] No se pudo instalar PyTorch. Si dice "Acceso denegado" o "WinError 32":
    echo      cierra Content App ^(tambien desde la bandeja^), reinicia el PC y vuelve a ejecutar.
    echo      Si dice que el archivo esta dañado, borra la carpeta %VENV%\descargas.
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
exit /b 0

:: ------------------------------------------------------------------
:: Cierra los procesos que usan el Python del motor (el motor de video
:: o su diagnóstico). Mientras están abiertos, Windows bloquea los archivos
:: de PyTorch y da "Acceso denegado" (WinError 5) al instalar o borrar.
:: ------------------------------------------------------------------
:liberar_venv
powershell -NoProfile -Command "$d = (Resolve-Path $env:VENV -ErrorAction SilentlyContinue).Path; if ($d) { $p = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $PID -and ( $_.Path -like ($d + '*') -or ( $_.Modules -and ($_.Modules | Where-Object { $_.FileName -like ($d + '*') }) ) ) }; foreach ($x in $p) { Write-Host ('  [*] Cerrando ' + $x.ProcessName + ' (PID ' + $x.Id + ') que bloqueaba archivos del motor'); Stop-Process -Id $x.Id -Force -ErrorAction SilentlyContinue }; if ($p) { Start-Sleep -Seconds 2 } }"
exit /b 0

