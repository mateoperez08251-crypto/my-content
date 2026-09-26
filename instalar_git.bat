@echo off
echo ===================================================
echo Instalador de Git para Windows (Virtual PC)
echo ===================================================
echo.
echo [INFO] Intentando instalar Git usando winget...
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] No se pudo instalar Git con winget. Intentando descarga manual...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.44.0.windows.1/Git-2.44.0-64-bit.exe' -OutFile 'Git-Installer.exe'"
    if exist Git-Installer.exe (
        echo [INFO] Ejecutando el instalador de Git de forma silenciosa...
        start /wait Git-Installer.exe /VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS
        echo [INFO] Limpiando instalador...
        del Git-Installer.exe
    ) else (
        echo [ERROR] No se pudo descargar el instalador de Git.
        pause
        exit /b 1
    )
)

echo.
echo [INFO] Git se ha instalado correctamente.
echo [INFO] Por favor, CIERRA esta ventana y abre una nueva antes de instalar las dependencias.
echo.
pause
