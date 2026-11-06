@echo off
rem Detiene el buzon y el tunel y quita el inicio automatico. No toca la cola ni los videos.
schtasks /Delete /F /TN "MyContent Buzon" >nul 2>&1
schtasks /Delete /F /TN "MyContent Tunel" >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MyContent_Buzon.vbs" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'buzon(_tunel)?\.py' -or ($_.Name -eq 'cloudflared.exe' -and $_.CommandLine -match '8787|buzon') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Buzon y tunel apagados.
if not "%~1"=="/silencio" pause
