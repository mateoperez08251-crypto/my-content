@echo off
rem Apaga la revision automatica. cola.json y los videos no se tocan.
schtasks /Delete /F /TN "MyContent Cola"
if errorlevel 1 (
    echo La tarea no existia o no se pudo borrar.
) else (
    echo Cola apagada.
)
if not "%~1"=="/silencio" pause
