@echo off
echo ===================================================
echo Instalador de Dependencias para VideoAI y Clonador
echo ===================================================
echo.

if not exist ".venv\Scripts\activate.bat" (
    echo [INFO] Creando entorno virtual .venv...
    python -m venv .venv
)

echo [INFO] Activando entorno virtual...
call .venv\Scripts\activate.bat

echo [INFO] Instalando dependencias de Python...
pip install --upgrade pip
pip install fastapi uvicorn flask requests flask-cors aiofiles
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install diffusers transformers accelerate realesrgan opencv-python pydub moviepy
echo [INFO] Instalando BasicSR desde GitHub (parche para Python moderno)...
pip install git+https://github.com/XPixelGroup/BasicSR
pip install pydub moviepy

echo.
echo [INFO] Dependencias instaladas con exito.
echo.
pause
