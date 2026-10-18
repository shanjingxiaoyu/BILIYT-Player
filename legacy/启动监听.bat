@echo off
REM ============================================================
REM  BiliYTPlayer — 剪贴板直连播放器
REM  双击启动，从源码运行
REM  前提: 已安装 Python 3 + pip install -r requirements.txt
REM ============================================================
cd /d "%~dp0"

echo 正在启动 BiliYTPlayer...
echo 支持: BV 视频 / EP 番剧电影 / YouTube 链接
echo.

if exist ".venv-tk\Scripts\python.exe" (
    .venv-tk\Scripts\python.exe -u bili_yt_player.pyw
) else if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -u bili_yt_player.pyw
) else (
    python -u bili_yt_player.pyw
)

pause
