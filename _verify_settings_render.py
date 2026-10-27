# -*- coding: utf-8 -*-
"""用**真实 SettingsBridge + 真实 SettingsWindow** 截图并断言控件可用。

为什么单独一个脚本：
    `_shot.py` 不注入任何上下文属性，之前的 `_shot_wrapper.qml` 只能配桩数据。
    桩是 QML 原生 JS 对象，永远「数据是好的」，所以「真实 bridge → QML 的
    QJSValue 通路坏了、ComboBox 全空」这类 bug 一张截图都测不出来（实际踩过）。
    本脚本把真实 bridge 注进去，走的就是 exe 里那条通路。

用法（_verify_settings.py 内部调用；也可单独跑）：
    python _scratch_shot_real.py <页签> <输出png> <light|dark>

退出码 0 = 全部断言通过；非 0 = 有控件不可用。
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "legacy"))

PAGE = sys.argv[1] if len(sys.argv) > 1 else "general"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / f"_real_{PAGE}.png"
SCHEME = sys.argv[3] if len(sys.argv) > 3 else "light"

from PySide6.QtCore import QObject, Qt, QUrl, QTimer
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

QQuickStyle.setStyle("FluentWinUI3")
app = QApplication(sys.argv[:1])
app.setQuitOnLastWindowClosed(False)

# 深浅色有两个开关，必须同时设，否则会出现「深色卡片 + 浅灰下拉框」的错配：
#   __forceScheme            → SettingsTheme.dark（我方 token，QML 里读）
#   styleHints.colorScheme   → Fluent 控件自身的配色（Qt 样式内部读）
# 只设前者的话，控件还是按系统（浅色）画，深色截图里就是浅灰下发灰的下拉框。
from PySide6.QtCore import Qt as _Qt

app.styleHints().setColorScheme(
    _Qt.ColorScheme.Dark if SCHEME == "dark" else _Qt.ColorScheme.Light)

from ui.settings_bridge import SettingsBridge

# 用临时配置目录，别污染用户真实的 %APPDATA%
cfg_dir = Path(tempfile.mkdtemp(prefix="biliyt_shot_"))
bridge = SettingsBridge(cfg_dir, log=lambda _m: None)
app._keep = bridge

engine = QQmlApplicationEngine()
engine.addImportPath(str(ROOT / "ui"))
engine.rootContext().setContextProperty("settingsBridge", bridge)
engine.rootContext().setContextProperty("__forceScheme", SCHEME)

warnings = []
engine.warnings.connect(lambda ws: warnings.extend(str(w) for w in ws))

engine.load(QUrl.fromLocalFile(str(ROOT / "ui" / "settings" / "SettingsWindow.qml")))
if not engine.rootObjects():
    print("LOAD FAIL")
    raise SystemExit(2)

win = engine.rootObjects()[-1]
win.setProperty("initialPage", PAGE)
win.show()

FAILS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def walk(obj, out):
    for child in obj.children():
        out.append(child)
        walk(child, out)


def run_checks():
    # 逐个页签切过去，把「看得见且真能弹」的 ComboBox 数出来
    pages = ["general", "playback", "subtitle", "sponsor"]
    summary = []
    for key in pages:
        win.setProperty("currentKey", key)
        app.processEvents()
        objs = []
        walk(win, objs)
        combos = [o for o in objs if "ComboBox" in o.metaObject().className()]
        usable = [c for c in combos
                  if c.property("count") > 0
                  and c.property("implicitHeight") > 0
                  and c.property("height") > 0]
        counts = sorted({c.property("count") for c in combos})
        summary.append((key, len(combos), len(usable), counts))
        # 每页至少要有 ComboBox 且全部可用（StackLayout 里非当前页会是 0，所以
        # 只在切到该页后统计）
        if key in ("general", "playback", "subtitle", "sponsor"):
            check(f"{key} 页 ComboBox 可用 ({len(usable)}/{len(combos)})",
                  len(combos) > 0 and len(usable) == len(combos),
                  f"counts={counts}")

    # 具体的期望值（真实 bridge 的数据）
    win.setProperty("currentKey", "playback")
    app.processEvents()
    objs = []
    walk(win, objs)
    qc = [o for o in objs if o.property("objectName") == "qualityCombo"]
    if qc:
        check("画质下拉 = 11 档", qc[0].property("count") == 11, f"count={qc[0].property('count')}")
    else:
        check("画质下拉存在", False, "找不到 qualityCombo")

    win.setProperty("currentKey", "sponsor")
    app.processEvents()
    QTimer.singleShot(600, after_sponsor)


def after_sponsor():
    # Repeater 生成的 delegate 在 Python 侧 findChildren 拿不到（实测：
    # 页面上明明画出了 11 个下拉，findChildren 一个都找不到）。所以这页改用
    # **截图取色**验证：「跳过方式」这一列必须有 11 段独立的深色文字像素。
    shot = win.grabWindow()
    tmp = ROOT / "_sponsor_tiers_probe.png"
    shot.save(str(tmp))
    bands = 0
    try:
        from PIL import Image
        im = Image.open(tmp).convert("RGB")
        w = im.width
        # 深色主题下文字是亮色、浅色主题下文字是暗色 —— 按背景亮度自动选阈值
        bg = im.getpixel((int(w * 0.5), 140))            # 卡片底色取样
        dark_theme = sum(bg) / 3 < 128
        # 「跳过方式」列大约在 x 44%~61%（980 宽窗口下的中间列）
        x0, x1 = int(w * 0.44), int(w * 0.61)
        prev_hit = False
        for y in range(150, min(im.height, 620)):
            hits = 0
            for x in range(x0, x1, 2):
                r, g, b = im.getpixel((x, y))
                if dark_theme:
                    if r > 150 and g > 150 and b > 150:  # 亮色文字
                        hits += 1
                else:
                    if r < 200 and g < 200 and b < 200:  # 暗色文字
                        hits += 1
            cur = hits >= 2
            if cur and not prev_hit:
                bands += 1
            prev_hit = cur
    except Exception as e:                                  # noqa: BLE001
        check("广告跳过行取色", False, repr(e))
    finally:
        tmp.unlink(missing_ok=True)
    check(f"广告跳过 11 行「跳过方式」已绘制（文字带数 {bands}）",
          bands >= 11, f"数到 {bands} 条文字带")

    # 截图（回到用户要求的页签）
    win.setProperty("currentKey", PAGE)
    app.processEvents()
    QTimer.singleShot(700, do_shot)


def do_shot():
    img = win.grabWindow()
    ok = img.save(str(OUT))
    check(f"截图落盘 {OUT.name}", ok and OUT.is_file() and OUT.stat().st_size > 2000,
          f"size={OUT.stat().st_size if OUT.exists() else 0}")
    print(f"\n共 {CHECKS[0]} 项检查，失败 {len(FAILS)} 项")
    if FAILS:
        print("FAIL " + str(len(FAILS)))
        raise SystemExit(1)
    print("ALL PASS")
    raise SystemExit(0)


QTimer.singleShot(1500, run_checks)
app.exec()
