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
tasklist /fi "imagename eq ContentAppPro.exe" 2>nul | find /i "ContentAppPro.exe" >nul
if not errorlevel 1 (
    echo  [!] Content App esta abierta. Cierrala ^(tambien desde la bandeja junto al reloj^)
    echo      y pulsa una tecla para seguir.
    pause >nul
)
call :liberar_venv
powershell -NoProfile -Command "$d = (Resolve-Path '.venv_video' -ErrorAction SilentlyContinue).Path; if ($d) { $p = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $PID -and ( $_.Path -like ($d + '*') -or ( $_.Modules -and ($_.Modules | Where-Object { $_.FileName -like ($d + '*') }) ) ) }; foreach ($x in $p) { Write-Host ('  [*] Cerrando ' + $x.ProcessName + ' (PID ' + $x.Id + ') que bloqueaba archivos de .venv_video'); Stop-Process -Id $x.Id -Force -ErrorAction SilentlyContinue }; if ($p) { Start-Sleep -Seconds 2 } }"
exit /b 0

:: ------------------------------------------------------------------
:: Cierra los procesos que usan el Python de .venv_video (el motor de video
:: o su diagnóstico). Mientras están abiertos, Windows bloquea los archivos
:: de PyTorch y da "Acceso denegado" (WinError 5) al instalar o borrar.
:: ------------------------------------------------------------------
:liberar_venv
powershell -NoProfile -Command "$p = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*\.venv_video\*' }; if ($p) { Write-Host ('  [*] Cerrando ' + $p.Count + ' proceso(s) del motor que bloqueaban archivos...'); $p | Stop-Process -Force; Start-Sleep -Seconds 2 }"
exit /b 0

