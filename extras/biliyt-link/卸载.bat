@echo off
cd /d "%~dp0"
echo.
echo   BiliYTPlayer Launcher - Uninstall
echo   --------------------------------
biliyt-forwarder.exe --uninstall
echo.
echo   Done. The biliyt:// protocol has been removed.
pause
