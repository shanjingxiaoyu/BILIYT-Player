@echo off
chcp 65001 >nul
REM ============================================================
REM  BiliYTPlayer — 打包为 Windows EXE
REM  用法: 在 legacy 目录下双击运行，或在终端中执行
REM  产物: dist\BiliYTPlayer.exe（并同步复制到 legacy\BiliYTPlayer.exe）
REM  硬约束: 虚拟环境必须基于系统完整版 Python 创建（带 tkinter）。
REM          严禁回退到缺少 tkinter 的 .venv，否则产出的 exe 双击即崩。
REM ============================================================
cd /d "%~dp0"

set "VENV=.venv-tk"
if not exist "%VENV%\Scripts\python.exe" (
    echo [!] 未找到 %VENV%，正在用系统 Python 创建...
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -m venv "%VENV%"
    ) else (
        py -3 -m venv "%VENV%" 2>nul || python -m venv "%VENV%"
    )
    if errorlevel 1 (
        echo [!] 创建 %VENV% 失败，请确认已安装系统版 Python 3.10+。
        exit /b 1
    )
)
echo [*] 使用虚拟环境: %VENV%

echo [*] 校验 tkinter 模块（防无界面精简版 Python 漏包）...
"%VENV%\Scripts\python.exe" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo [!] 致命错误：当前虚拟环境 %VENV% 缺少 tkinter 模块！
    echo     请删除 %VENV% 目录，改用系统完整版 Python 重新创建虚拟环境。
    exit /b 1
)

echo [*] 检查 PyInstaller ...
"%VENV%\Scripts\pip.exe" show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [!] PyInstaller 未安装，正在安装...
    "%VENV%\Scripts\pip.exe" install pyinstaller
    if errorlevel 1 (
        echo [!] PyInstaller 安装失败。
        exit /b 1
    )
)

echo [*] 检查依赖 ...
"%VENV%\Scripts\pip.exe" install -r requirements.txt >nul
if errorlevel 1 (
    echo [!] 依赖安装失败，请检查上方错误信息或网络连接。
    exit /b 1
)

echo [*] 清理旧构建产物 ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [*] 开始打包 ...
set SAFE_DELETE_DISABLE=1
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm bili_yt_player.spec
if errorlevel 1 (
    echo [!] 打包失败，请检查上方错误信息。
    exit /b 1
)

echo.
echo ============================================================
echo [+] 打包完成！
echo     输出: dist\BiliYTPlayer.exe
for %%A in ("dist\BiliYTPlayer.exe") do echo     大小: %%~zA bytes
echo.
echo [*] 复制到 legacy 目录...
copy /y dist\BiliYTPlayer.exe BiliYTPlayer.exe >nul
echo [+] legacy\BiliYTPlayer.exe 已更新
echo ============================================================

if "%BILIYT_NONINTERACTIVE%"=="" pause
