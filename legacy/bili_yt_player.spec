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
    # 跨文件动态导入（bili_yt_player.pyw / bili_clipboard_dolby.py 内部延迟 import）
    "bili_clipboard_dolby",
    # SponsorBlock 广告跳过（在 launch_player 与 GUI 中延迟导入，
    # 静态分析看不到，必须显式声明，否则打包后跳过功能静默失效）
    "sponsorblock",
    # 受管 OSC 补丁器（同上）
    "mpv_osc",
    # 弹幕 / 字幕（GUI 与播放路径中延迟导入）
    "danmaku",
    "subtitle",
    # tkinter 延迟导入
    "tkinter.filedialog",
    "tkinter.messagebox",
    # pyperclip 平台后端
    "pyperclip",
    # requests 底层依赖
    "urllib3",
    "charset_normalizer",
    "certifi",
    "idna",
]

# ---- 需要随 exe 一起打包的数据文件 ----
# (源路径, 目标目录名)
# 注意：bsponsor.lua / osc_managed.lua / 弹幕 ASS 都是运行时生成的，无需打包；
#       但 osc_base.lua 是打补丁的基线，必须随包分发，否则彩色进度条不可用。
_add_datas = [
    # 确保模块与基线数据在导入路径中
    ("bili_clipboard_dolby.py", "."),
    ("sponsorblock.py", "."),
    ("mpv_osc.py", "."),
    ("danmaku.py", "."),
    ("subtitle.py", "."),
    ("osc_base.lua", "."),
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
