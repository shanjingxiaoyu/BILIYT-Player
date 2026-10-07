# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["cb_to_mpv.pyw"],
    pathex=[],
    binaries=[],
    datas=[],
    # qrcode 的图像插件（PIL / PNG）是运行期按 factory 名字动态导入的，
    # 静态分析抓不到。我们只走 get_matrix() + Canvas 绘制，碰不到那些分支，
    # 但把包本身收进来，否则打包版一按「扫码登录」就 ImportError。
    hiddenimports=["configparser", "qrcode", "qrcode.image", "qrcode.constants"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CB-to-MPV",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
