# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 — BiliYTPlayer QML 版

用法（在仓库根目录）:
    python -m PyInstaller --noconfirm build_qml.spec
输出 dist/BiliYTPlayer.exe，与 mpv-portable/ 同级放置。

体积关键（ui-kit/README.md「打包注意事项」实测）：
  * PySide6 的 PyInstaller hook 会无差别收集全部 Qt DLL，`excludes` 对二进制无效，
    必须手动过滤 a.binaries。不做这步 → Qt6WebEngineCore.dll 单独 190 MB → exe 150 MB。
  * `FluentWinUI3/qmldir` 里 `import QtQuick.Controls.Fusion auto`，
    所以 Fusion **不能裁**（裁了运行时报 LOAD FAIL，静默无有用信息）；
    Basic **不能裁**（裁了 Fluent 控件回退到 Basic 实现）。
  * 实测：全收 150.21 MB / 只裁 WebEngine+Pdf 64.51 MB / 全裁 ≈48.8 MB。
"""

import sys
from pathlib import Path

_root = Path(SPECPATH).resolve()
_ui = _root / "ui"

_block_cipher = None

# ---- 动态 import，静态分析看不到 ----
_hidden_imports = [
    # 后端核心（bridge.py 内延迟 import）
    "bili_clipboard_dolby",
    "sponsorblock",
    "mpv_osc",
    "danmaku",
    "subtitle",
    # 新增应用层
    # 注意：必须同时列出包本体 `app` / `ui`，只列子模块时 PyInstaller 不会把
    # 包的其余成员收集进去（实测第一版 exe 里 `app/` 完全缺失 → 打包后启动即
    # 静默无窗口，因为 --windowed 下 import 失败没有任何可见输出）。
    "app",
    "app.app_config",
    "app.quality",
    "app.autostart",
    "app.tray",
    "app.theme",
    "app.single_instance",
    "ui",
    "ui.bridge",
    "ui.settings_bridge",
    # PySide6 子模块（QML 运行时按需 import，静态分析拿不全）
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQml",
    "PySide6.QtWidgets",
    "PySide6.QtGui",
    "PySide6.QtCore",
    "PySide6.QtNetwork",
    # 平台后端（托盘菜单需要 widgets 平台插件）
    "pyperclip",
    # requests 底层
    "urllib3",
    "charset_normalizer",
    "certifi",
    "idna",
]

# ---- 随包分发的数据 ----
_add_datas = [
    # 后端模块源码（bridge 用 sys.path 从 legacy/ 导入）
    (str(_root / "legacy" / "bili_clipboard_dolby.py"), "legacy"),
    (str(_root / "legacy" / "sponsorblock.py"), "legacy"),
    (str(_root / "legacy" / "mpv_osc.py"), "legacy"),
    (str(_root / "legacy" / "danmaku.py"), "legacy"),
    (str(_root / "legacy" / "subtitle.py"), "legacy"),
    # OSC 补丁基线（缺了就退化为 mpv 内置 OSC）
    (str(_root / "legacy" / "osc_base.lua"), "legacy"),
    # 全部 QML（含 euikit 与 settings 子目录）
    (str(_ui), "ui"),
]

a = Analysis(
    ["bili_yt_player_qml.py"],
    pathex=[str(_root), str(_root / "legacy")],
    binaries=[],
    datas=_add_datas,
    hiddenimports=_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # ---- 标准库测试/文档 ----
        "tkinter",          # QML 版不用 tkinter
        "test", "unittest", "pydoc", "doctest", "lib2to3", "distutils",
        # ---- 本机 site-packages 里的重型第三方包 ----
        # 本机 Python 环境装了 numpy/PyQt5/pygame/PIL/scipy/matplotlib/pandas 等
        # 一大堆与本项目无关的包，PyInstaller 的 hook 会顺着模块图把它们全拖进来
        # （实测第一版 exe 123 MB，其中 numpy.libs/libopenblas 单个就 36 MB，
        # 还混进了整个 PyQt5 和 pygame）。项目代码对这些的引用是零 —— 核对过
        # legacy/{danmaku,sponsorblock,subtitle,mpv_osc,bili_clipboard_dolby}.py
        # 与 ui/ app/ 的全部 import，只有 requests / pyperclip / dotenv / PySide6。
        "numpy", "scipy", "pandas", "matplotlib", "sympy", "sklearn",
        "PyQt5", "PyQt6", "PySide2", "pygame", "PIL", "Pillow",
        "cryptography", "OpenSSL", "nacl", "bcrypt",
        "IPython", "jupyter", "notebook", "nbformat",
        "pytest", "hypothesis", "sphinx", "docutils",
        "cv2", "imageio", "skimage", "tifffile",
        "lxml", "bs4", "html5lib", "soupsieve",
        "zmq", "tornado", "pika", "redis", "pymongo",
        "sqlalchemy", "alembic",
        "setuptools", "pkg_resources", "wheel", "pip",
        "paste", "paste.deploy", "repoze", "repoze.lru",
        # ---- 明确不用的重型 Qt 模块（excludes 只影响 Python 模块，DLL 靠下面过滤）----
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
        "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
        "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
        "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
        "PySide6.QtOpenGLWidgets", "PySide6.QtRemoteObjects",
        "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtSpatialAudio",
        "PySide6.QtStateMachine", "PySide6.QtWebChannel",
        "PySide6.QtWebSockets", "PySide6.QtHttpServer",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=_block_cipher,
    noarchive=False,
)

# ============================================================
# 手动过滤 binaries —— 这一步决定 exe 是 48 MB 还是 150 MB
# ============================================================
#: 命中的 DLL / 目录一律丢弃（大小写不敏感）
_DROP_FRAGMENTS = [
    # 头像：WebEngine 单文件就 190 MB
    "qt6webengine", "qt6webview", "qt6webchannel", "qt6websockets", "qt6webenginecore",
    # PDF
    "qt6pdf",
    # 3D
    "qt6quick3d", "qt63d", "assimp",
    # Charts / 数据可视化
    "qt6charts", "qt6datavisualization",
    # 多媒体（播放全在 mpv，QML 侧不用）
    "qt6multimedia", "qt6spatialaudio",
    # 不用到的子系统
    "qt6bluetooth", "qt6nfc", "qt6positioning", "qt6serialport",
    "qt6sql", "qt6test", "qt6help", "qt6designer", "qt6uitools",
    "qt6remoteobjects", "qt6scxml", "qt6sensors", "qt6statemachine",
    "qt6httpserver",
    # 软件 OpenGL 兜底（8.8 MB，机器没有可用 GPU 时才有用；本程序不渲染 3D）
    "opengl32sw",
    # 插件目录里的重型后端
    "qtvirtualkeyboard", "qmltooling", "qt6virtualkeyboard",
    # 未使用的 QtQuick.Controls 样式（Fluent 只用 FluentWinUI3 + Fusion + Basic）
    "qtquickcontrols2imagine", "qtquickcontrols2material",
    "qtquickcontrols2universal", "qtquickcontrols2windows",
    # 未使用的 Qt 子系统（体积排行里剩下的几个）
    "qt6graphs", "qt6location", "qt6quickdialogs2quickimpl",
    "qt6quickdialogs2", "qt6positioningquick",
    # Python 侧 OpenGL 绑定（QML 渲染走 RHI，不经过这个模块）
    "qtopenGL.pyd",
]

#: 不能裁的样式（Fluent 依赖 Fusion 作为回退，Basic 是 Fluent 的实现基础）
#: README 实测：裁 Fusion → 运行时 LOAD FAIL（静默无有用信息）；裁 Basic → 控件回退
_KEEP_STYLES = ("fusion", "basic", "fluentwinui3")

#: Controls/ 下的非样式子目录，必须保留：
#:   impl     —— 所有 Fluent/Basic 控件的实现，缺了每个控件都报
#:               module "QtQuick.Controls.impl" is not installed
#:   designer —— Qt Designer 集成
_KEEP_EXTRA_DIRS = ("impl", "designer")

#: 按路径前缀丢弃的二进制/数据（本机 site-packages 里的无关重包）
#: excludes 对「已被 hook 复制进 binaries 的 DLL/pyd」同样无效，必须按路径再滤一遍：
#:   numpy.libs/libopenblas...dll  36.4 MB
#:   cryptography/.../_rust.pyd    9.4 MB
#:   PIL/_avif...pyd               7.5 MB
#:   PyQt5/Qt5/bin/*.dll          ~30 MB
_DROP_PATH_PREFIXES = (
    "numpy", "numpy.libs", "scipy", "pandas", "matplotlib", "mpl_toolkits",
    "pyqt5", "pyqt6", "pyside2", "pygame", "pil", "pillow",
    "cryptography", "openssl", "nacl", "bcrypt", "cffi", "_cffi_backend",
    "ipython", "jupyter", "notebook", "nbformat", "nbconvert",
    "pytest", "_pytest", "hypothesis", "sphinx", "docutils",
    "cv2", "imageio", "skimage", "tifffile",
    "lxml", "bs4", "html5lib", "soupsieve",
    "zmq", "tornado", "pika", "redis", "pymongo",
    "sqlalchemy", "alembic", "paste", "repoze",
    "setuptools", "pkg_resources", "wheel", "pip", "_distutils_hack",
    "test", "unittest", "lib2to3", "distutils",
    "tkinter", "_tkinter", "tcl", "tk",
)


def _drop_path(dest: str) -> str | None:
    """命中返回丢弃原因，否则 None。只看包路径前缀，不误伤 Qt/python 本体。"""
    low = dest.lower().replace("\\", "/")
    head = low.split("/", 1)[0]
    for pref in _DROP_PATH_PREFIXES:
        if head == pref or head.startswith(pref + ".") or head.startswith(pref + "/"):
            return pref
    # cryptography 的 .pyd 在 PySide6 之外，但路径里含 .dist-info 的跳过
    if ".dist-info/" in low or ".egg-info/" in low:
        return "meta"
    return None


_kept, _dropped = [], []
for entry in a.binaries:
    dest = entry[0].lower().replace("\\", "/")
    if any(frag in dest for frag in _DROP_FRAGMENTS):
        _dropped.append(entry[0])
        continue
    if _drop_path(entry[0]):
        _dropped.append(entry[0])
        continue
    _kept.append(entry)
a.binaries = _kept

# datas 里同样清一遍（site-packages 的 .dist-info / 测试数据）
_kept_datas, _dropped_datas = [], []
for entry in a.datas:
    if _drop_path(entry[0]):
        _dropped_datas.append(entry[0])
        continue
    _kept_datas.append(entry)
a.datas = _kept_datas

# ============================================================
# 手动过滤 QtQuick/Controls 的样式目录（Material/Universal/Imagine/Windows 可裁）
# ============================================================
def _keep_qml_dir(dest: str) -> bool:
    """QtQuick/Controls/<Style> 下只保留 Fluent 需要的样式。"""
    low = dest.lower().replace("\\", "/")
    if "qtquick/controls/" in low:
        tail = low.split("qtquick/controls/", 1)[1]
        # 只有「子目录」才代表样式名。Controls/ 根下的文件（qmldir /
        # plugins.qmltypes / qtquickcontrols2plugin.dll）是模块本身，丢了会
        # 报 plugin "qtquickcontrols2plugin" not found，整个界面加载失败。
        if "/" in tail:
            seg = tail.split("/")[0]
            if seg and seg not in _KEEP_STYLES and seg not in _KEEP_EXTRA_DIRS:
                return False
    return True


_style_dropped = []


def _filter_style(entries):
    kept = []
    for entry in entries:
        if _keep_qml_dir(entry[0]):
            kept.append(entry)
        else:
            _style_dropped.append(entry[0])
    return kept


a.datas = _filter_style(a.datas)
a.binaries = _filter_style(a.binaries)

pyz = PYZ(a.pure, a.zipped_data, cipher=_block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="BiliYTPlayer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                  # --windowed：双击无控制台
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="app.ico",
)

print(f"[build_qml] 丢弃 binaries {len(_dropped)} 项，丢弃样式/QML 目录 {len(_style_dropped)} 项")
