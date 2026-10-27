# -*- coding: utf-8 -*-
"""shell-agent 三件套自检：autostart / tray / CloseDialog。

用法:
    python _verify_shell.py

会真的写注册表项再**清理掉**（测完不留垃圾）；托盘用 offscreen 平台验证构造；
CloseDialog 用 _shot.py 离屏截图验证。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，中文断言名会乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f"  ({detail})" if detail else ""))


# ============================================================
# 1. autostart
# ============================================================
def verify_autostart() -> None:
    print("\n=== 1. autostart（注册表 HKCU Run）===")
    from app import autostart as a

    # 源码运行时 current_exe() 必须是 None
    check("current_exe() 源码运行返回 None", a.current_exe() is None,
          f"frozen={getattr(sys, 'frozen', False)}")

    opts = a.options()
    check("options() 长度 == 2", len(opts) == 2, str(opts))
    check("options() 内容正确",
          opts == (("none", "仅手动启动"), ("registry", "开机自启")), str(opts))

    # is_enabled 不能抛（非 registry 模式 / 注册表项缺失都要安全）
    try:
        check("is_enabled('registry') 不抛", a.is_enabled("registry") is False,
              "当前无该项，应为 False")
    except Exception as e:  # noqa: BLE001
        check("is_enabled('registry') 不抛", False, f"抛了 {type(e).__name__}: {e}")
    check("is_enabled('none') == False", a.is_enabled("none") is False)

    # 值解析：带引号/不带引号、路径含空格都要能剥出 exe
    check("_stored_exe 解析带引号值",
          a._stored_exe(r'"C:\tmp\fake x\BiliYTPlayer.exe" --autostart')
          == r"C:\tmp\fake x\BiliYTPlayer.exe",
          a._stored_exe(r'"C:\tmp\fake x\BiliYTPlayer.exe" --autostart'))
    check("_stored_exe 解析无引号旧值",
          a._stored_exe(r"C:\App\BiliYTPlayer.exe --autostart") == r"C:\App\BiliYTPlayer.exe",
          a._stored_exe(r"C:\App\BiliYTPlayer.exe --autostart"))

    # apply("registry", None) 必须返回 (False, ...) 而不是抛
    try:
        ok, msg = a.apply("registry", None)
        check("apply('registry', None) 返回 (False, 原因)", ok is False and bool(msg), msg)
    except Exception as e:  # noqa: BLE001
        check("apply('registry', None) 返回 (False, 原因)", False,
              f"抛了 {type(e).__name__}: {e}")

    # 真写一次再删，验证 is_enabled 的读写闭环
    fake = r"C:\tmp\fake x\BiliYTPlayer.exe"
    try:
        ok, msg = a.apply("registry", fake)
        check("apply('registry', 假路径) 成功", ok is True, msg)
        if ok:
            # 源码运行时 current_exe() is None → 项存在即视为启用
            check("apply 后 is_enabled('registry') == True", a.is_enabled("registry") is True)

            # 注册表里的值形态要对（带引号 + --autostart）
            if a.winreg is not None:
                raw = a._read()
                expect = f'"{fake}" --autostart'
                check("注册表值格式正确", raw == expect, repr(raw))

            ok2, msg2 = a.apply("none", fake)
            check("apply('none') 关闭成功", ok2 is True, msg2)
            check("关闭后 is_enabled('registry') == False",
                  a.is_enabled("registry") is False)
    finally:
        # 无论如何都清一遍，绝不留垃圾
        a.apply("none", None)
        leftover = a._read() if a.winreg is not None else None
        check("清理完毕（注册表无残留）", leftover is None, repr(leftover))


# ============================================================
# 2. tray
# ============================================================
def verify_tray() -> None:
    print("\n=== 2. tray（QSystemTrayIcon）===")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from app.tray import Tray

    app = QApplication.instance() or QApplication(sys.argv)
    _ = app  # 保持引用，防止被 GC 掉导致后续控件崩

    # 无 QApplication 时的防御分支（此时已有 app，只能查代码路径不崩）
    tray = Tray()
    check("Tray() 构造不崩", tray is not None)

    avail = tray.available
    check("available 可读（bool）", isinstance(avail, bool), f"available={avail}")

    check("菜单项数量 == 3", len(tray.menu_actions) == 3,
          str([x.text() for x in tray.menu_actions]))
    check("菜单项文字正确",
          [x.text() for x in tray.menu_actions] == ["显示主窗口", "设置", "退出"],
          str([x.text() for x in tray.menu_actions]))

    # 无托盘环境下这些必须是静默 no-op
    try:
        tray.show()
        tray.setTip("测试提示")
        tray.notify("标题", "内容")
        tray.hide()
        check("show/setTip/notify/hide 无托盘不崩", True)
    except Exception as e:  # noqa: BLE001
        check("show/setTip/notify/hide 无托盘不崩", False, f"{type(e).__name__}: {e}")

    # 信号能正常发出
    from PySide6.QtWidgets import QSystemTrayIcon

    got = []
    tray.showRequested.connect(lambda: got.append("show"))
    tray.quitRequested.connect(lambda: got.append("quit"))
    tray.settingsRequested.connect(lambda: got.append("settings"))
    tray._on_activated(QSystemTrayIcon.DoubleClick)
    check("双击 → showRequested", got == ["show"], str(got))

    # 图标确实画出来了（16 / 32 两档）
    icon = tray._icon.icon() if tray._icon else None
    sizes = sorted(s.width() for s in icon.availableSizes()) if icon else []
    check("图标有 16/32 两档位图", sizes == [16, 32], str(sizes))

    # 防御分支：只有 QGuiApplication（没有 QApplication）时必须是空壳，不能崩。
    # 这条拦的是 `QApplication.instance() is None` 这种写法 —— 只建 QGuiApplication
    # 时 instance() 不是 None，但 QMenu() 会直接崩在 C++ 侧。
    guard = subprocess.run(
        [sys.executable, str(ROOT / "_verify_tray_guard.py")],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    check("无 QApplication 时 Tray 退化为空壳不崩",
          "GUARD OK" in guard.stdout,
          (guard.stdout + guard.stderr).replace("\n", " ").strip()[-160:])


# ============================================================
# 3. CloseDialog.qml 截图
# ============================================================
# 行为探针：ApplicationWindow 当"主窗口"，CloseDialog 传 dialogParent 后 open()，
# 断言居中坐标 = 父窗口位置 + (父尺寸 - 对话框尺寸)/2。
# Window 没有 mapToGlobal，这条能拦住"对 Window 调 Item 方法"的回归。
_CENTER_PROBE = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
QQuickStyle.setStyle("FluentWinUI3")
here = Path(__file__).resolve().parent
app = QGuiApplication([])
eng = QQmlApplicationEngine()
eng.addImportPath(str(here))
eng.rootContext().setContextProperty("__forceScheme", "light")
eng.load(QUrl.fromLocalFile(str(here / "_probe_center.qml")))
if not eng.rootObjects():
    print("LOAD FAIL"); raise SystemExit(1)
w = eng.rootObjects()[0]
app.processEvents()
ok = (w.property("opened")
      and w.property("dx") == 340 and w.property("dy") == 222
      and w.property("tp") is not None)
print("CENTER OK" if ok else "CENTER MISMATCH dx=%s dy=%s tp=%s" % (
    w.property("dx"), w.property("dy"), w.property("tp")))
'''.lstrip()

_CENTER_QML = r'''
import QtQuick
import QtQuick.Controls
import QtQuick.Window
import ".."

ApplicationWindow {
    id: main
    visible: true
    width: 900
    height: 600
    x: 100
    y: 80

    CloseDialog {
        id: dlg
        dialogParent: main
    }
    Component.onCompleted: dlg.open()
    property bool opened: dlg.visible
    property int dx: dlg.x
    property int dy: dlg.y
    property var tp: dlg.transientParent
}
'''.lstrip()


def verify_closedialog() -> None:
    print("\n=== 3. CloseDialog.qml（离屏截图）===")
    qml = ROOT / "ui" / "CloseDialog.qml"
    check("ui/CloseDialog.qml 存在", qml.exists())

    for scheme, out in (("light", "_closedlg_light.png"), ("dark", "_closedlg_dark.png")):
        dst = ROOT / out
        if dst.exists():
            dst.unlink()
        r = subprocess.run(
            [sys.executable, "_shot.py", "ui/CloseDialog.qml", out, "420", "316", scheme],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        ok = r.returncode == 0 and dst.exists() and dst.stat().st_size > 5000
        check(f"截图 {scheme} 成功", ok,
              f"{r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()}")

    # 行为 + 居中：dialogParent 是 Window（Lead 的主窗口）时不能调 mapToGlobal
    wrapper = ROOT / "ui" / "_probe_center.qml"
    probe = ROOT / "ui" / "_probe_center.py"
    wrapper.write_text(_CENTER_QML, encoding="utf-8")
    probe.write_text(_CENTER_PROBE, encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, str(probe)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=120)
        out = r.stdout
        check("CloseDialog 行为与居中（父为 Window）", "CENTER OK" in out,
              out.replace("\n", " ").strip()[-160:] or r.stderr.strip()[-160:])
    finally:
        wrapper.unlink(missing_ok=True)
        probe.unlink(missing_ok=True)


def main() -> int:
    print(f"Python {sys.version.split()[0]}  |  {ROOT}")
    verify_autostart()
    verify_tray()
    verify_closedialog()

    print()
    if FAIL == 0:
        print("ALL PASS")
        return 0
    print(f"FAIL {FAIL}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
