@echo off
cd /d "%~dp0"
echo.
echo   BiliYTPlayer Launcher - Install
echo   --------------------------------
echo   Tip: you can also drag BiliYTPlayer.exe onto this .bat
echo.
if "%~1"=="" (
    biliyt-forwarder.exe --install
) else (
    echo   Using player: %~1
    biliyt-forwarder.exe --install "%~1"
)
echo.
echo   Done. See 安装结果.txt for details.
pause
