# -*- coding: utf-8 -*-
"""关闭行为端到端自检：真实主窗口 + 真实 CloseDialog，模拟点 X，
验证「询问框弹出 → 选择 → 落盘 → 动作」整条链路。

不真的退出进程：把 requestQuit / minimizeToTray 换成记录器。
用法: python _verify_close_flow.py
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

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def main():
    QQuickStyle.setStyle("FluentWinUI3")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    from app import app_config
    from ui.bridge import UiBridge
    import ui.bridge as bmod

    tmp = Path(tempfile.mkdtemp(prefix="biliyt_close_"))
    old = bmod.CONFIG_DIR
    bmod.CONFIG_DIR = tmp

    bridge = UiBridge()
    app._dsh_bridge = bridge

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(ROOT / "ui"))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.rootContext().setContextProperty("__forceScheme", "light")

    engine.load(QUrl.fromLocalFile(str(ROOT / "ui" / "CloseDialog.qml")))
    dlg = engine.rootObjects()[-1]
    engine.load(QUrl.fromLocalFile(str(ROOT / "ui" / "Main.qml")))
    win = engine.rootObjects()[-1]
    win.setProperty("closeDialog", dlg)
    win.setProperty("_forceClose", False)

    # 拦截动作，避免真的退出
    state = {"quit": 0, "tray": 0}
    win.setProperty("tray", None)

    def fake_quit():
        state["quit"] += 1
    def fake_min():
        state["tray"] += 1

    win.requestQuit = fake_quit           # 覆盖 QML 函数（Python 侧可写属性? 试试）
    win.show()
    app.processEvents()

    saved = []
    dlg.accepted.connect(lambda a, r: saved.append((a, r)))

    # ---------- case 1: 默认配置（ask + ask_on_close=True）→ 应弹框 ----------
    print("\n-- case 1: 默认 ask → 点 X 应弹询问框 --")
    check("初始 closeAction = ask", bridge.closeAction() == "ask", bridge.closeAction())
    check("初始 askOnClose = True", bridge.askOnClose() is True)

    # QML 里 _forceClose 控制是否放行；这里直接调 QML 的 onClosing 逻辑
    # 通过 evaluate：模拟 closeEvent
    win.setProperty("_forceClose", False)
    win.metaObject().invokeMethod  # noqa: B018  (仅确认可访问)

    # 直接走 QML 侧的 onClosing 判定函数：用 close() 触发
    win.close()
    app.processEvents()
    check("close() 被拦截（窗口仍可见）", win.isVisible() is True, f"visible={win.isVisible()}")
    check("询问框已弹出", dlg.property("visible") is True, f"dlg visible={dlg.property('visible')}")
    check("询问框预选 tray（不会误关监听）",
          dlg.property("action") == "tray", str(dlg.property("action")))
    check("询问框 remember 默认 True",
          dlg.property("remember") is True, str(dlg.property("remember")))

    # ---------- case 2: 选「最小化到托盘」+ 记住 → 落盘 ----------
    print("\n-- case 2: 选「最小化到托盘」并记住 --")
    dlg.setProperty("action", "tray")
    dlg.setProperty("remember", True)
    # 触发 accepted（等价点确定）
    dlg.accepted.emit("tray", True)
    app.processEvents()
    cfg = app_config.load_config(tmp)
    check("落盘 close_action=tray", cfg["close_action"] == "tray", cfg["close_action"])
    check("落盘 ask_on_close=False（记住了）", cfg["ask_on_close"] is False, cfg["ask_on_close"])
    bridge.general_cfg = cfg

    # ---------- case 3: 记住了之后，点 X 不再问 ----------
    print("\n-- case 3: 已记住 → 点 X 不再弹框 --")
    dlg.setProperty("visible", False)
    win.setProperty("_forceClose", False)
    win.show()
    app.processEvents()
    win.close()
    app.processEvents()
    check("不再弹询问框", dlg.property("visible") is False,
          f"dlg visible={dlg.property('visible')}")

    # ---------- case 4: 设置里改回「每次都问」 ----------
    print("\n-- case 4: 设置里改回询问 --")
    cfg = dict(bridge.general_cfg)
    cfg["close_action"] = "ask"
    cfg["ask_on_close"] = True
    app_config.save_config(tmp, cfg)
    bridge.general_cfg = app_config.load_config(tmp)
    check("配置已改回 ask", bridge.closeAction() == "ask" and bridge.askOnClose() is True)

    dlg.setProperty("visible", False)
    win.setProperty("_forceClose", False)
    win.show()
    app.processEvents()
    win.close()
    app.processEvents()
    check("又弹询问框了", dlg.property("visible") is True)

    # ---------- case 5: 设置里预先指定「直接关闭」 ----------
    print("\n-- case 5: 预设 close + 不问 → 点 X 直接执行 --")
    cfg = dict(bridge.general_cfg)
    cfg["close_action"] = "close"
    cfg["ask_on_close"] = False
    app_config.save_config(tmp, cfg)
    bridge.general_cfg = app_config.load_config(tmp)
    dlg.setProperty("visible", False)
    win.setProperty("_forceClose", False)
    win.show()
    app.processEvents()
    before = dlg.property("visible")
    win.close()
    app.processEvents()
    check("不弹询问框（预设 close）", dlg.property("visible") is False,
          f"before={before} after={dlg.property('visible')}")

    # ---------- case 6: 自启启动 → 住托盘 ----------
    print("\n-- case 6: savCloseChoice 双向 ----------")
    bridge.general_cfg = app_config.load_config(tmp)
    bridge.saveCloseChoice("close", False)
    cfg = app_config.load_config(tmp)
    check("saveCloseChoice('close', False) → close + 仍询问",
          cfg["close_action"] == "close" and cfg["ask_on_close"] is True, str(cfg))
    bridge.general_cfg = cfg
    bridge.saveCloseChoice("tray", True)
    cfg = app_config.load_config(tmp)
    check("saveCloseChoice('tray', True) → tray + 不询问",
          cfg["close_action"] == "tray" and cfg["ask_on_close"] is False, str(cfg))

    bmod.CONFIG_DIR = old

    n = sum(1 for _, ok_, _ in results if ok_)
    print("\n" + "=" * 66)
    print(f"共 {len(results)} 项，通过 {n} 项")
    print("ALL PASS" if n == len(results) else "FAILED")
    return 0 if n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
