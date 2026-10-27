# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置 — BiliYTPlayer GUI 版
用法: pyinstaller bili_yt_player.spec
"""

import sys
from pathlib import Path

_block_cipher = None

# ---- 需要额外加入的隐藏导入 ----
# 这些模块 PyInstaller 静态分析可能遗漏
_hidden_imports = [
    # 跨文件动态导入（bili_yt_player.pyw 内部 import bili_clipboard_dolby）
    "bili_clipboard_dolby",
    # tkinter 延迟导入
    "tkinter.filedialog",
    # pyperclip 平台后端
    "pyperclip",
    # 扫码登录的二维码生成（pyw 函数内 import，显式声明防漏打包——
    # 漏打包的表现：扫码线程静默异常 + wait_window 永久假死，用户只能
    # 任务管理器杀进程。2026-10-27 成品化审查 B-3 记录）
    "qrcode",
    # requests 底层依赖
    "urllib3",
    "charset_normalizer",
    "certifi",
    "idna",
]

# ---- 需要随 exe 一起打包的数据文件 ----
# (源路径, 目标目录名)
_add_datas = [
    # 确保 bili_clipboard_dolby.py 在导入路径中
    ("bili_clipboard_dolby.py", "."),
]

a = Analysis(
    ["bili_yt_player.pyw"],
    pathex=[],
    binaries=[],
    datas=_add_datas,
    hiddenimports=_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 精简体积：排除不用的标准库测试/文档
        "tkinter.test",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=_block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=_block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="BiliYTPlayer",                   # 输出 exe 名称
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                               # UPX 压缩减小体积
    upx_exclude=[],                         # 不排除任何文件
    runtime_tmpdir=None,
    console=False,                          # --windowed: 双击无控制台窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # ---- 版本信息（可选，放同目录 version_info.txt 或直接写在这里） ----
    # version="./version_info.txt",
    # icon="app.ico",                       # 替换为你的 .ico 文件路径
)
