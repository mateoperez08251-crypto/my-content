@echo off
chcp 65001 >nul
title Content App - Servidor del bot
cd /d "%~dp0"
echo.
echo  Content App en modo servidor (bot de subida + radar)
echo  Desde otra PC de tu casa abre:  http://IP-DE-ESTA-PC:5001/bot
echo  La contrasena sale abajo. No cierres esta ventana.
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo  IP de esta PC:%%a
echo.
py content.py --servidor
pause
