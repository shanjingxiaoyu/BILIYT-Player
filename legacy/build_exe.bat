@echo off
REM ============================================================
REM  BiliYTPlayer — 打包为 Windows EXE
REM  用法: 在 legacy 目录下双击运行，或在终端中执行
REM  产物: dist\BiliYTPlayer.exe
REM  注意: 虚拟环境必须基于系统版 Python 创建（带 tkinter）。
REM        WorkBuddy 托管版 Python 缺 tkinter，打出来的 exe 启动即崩。
REM ============================================================
cd /d "%~dp0"

set "VENV=.venv-tk"
if not exist ".venv-tk\Scripts\python.exe" (
    if exist ".venv\Scripts\python.exe" (
        set "VENV=.venv"
    ) else (
        echo [!] 未找到虚拟环境，正在创建 .venv-tk ...
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -m venv .venv-tk
        if errorlevel 1 (
            echo [!] 系统 Python313 创建失败，回退 PATH 中的 python ...
            python -m venv .venv-tk
        )
        set "VENV=.venv-tk"
    )
)
echo [*] 使用虚拟环境: %VENV%

echo [*] 检查 PyInstaller ...
%VENV%\Scripts\pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] PyInstaller 未安装，正在安装...
    %VENV%\Scripts\pip install pyinstaller
)

echo [*] 检查依赖 ...
%VENV%\Scripts\pip install -r requirements.txt >nul 2>&1

echo [*] 清理旧构建产物 ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [*] 开始打包 ...
set SAFE_DELETE_DISABLE=1
%VENV%\Scripts\python.exe -m PyInstaller --noconfirm bili_yt_player.spec

if %errorlevel% equ 0 (
    echo.
    echo ============================================================
    echo [+] 打包完成！
    echo     输出: dist\BiliYTPlayer.exe
    for %%A in ("dist\BiliYTPlayer.exe") do echo     大小: %%~zA bytes
    echo.
    echo [*] 复制到根目录...
    copy /y dist\BiliYTPlayer.exe BiliYTPlayer.exe >nul
    echo [+] 根目录 BiliYTPlayer.exe 已更新
    echo ============================================================
) else (
    echo [!] 打包失败，请检查上方错误信息。
)

pause
