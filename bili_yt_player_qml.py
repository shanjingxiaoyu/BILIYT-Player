#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bili_yt_player_qml.py — 剪贴板直连播放器（PySide6 + QML 入口）

替换原 tkinter 入口 legacy/bili_yt_player.pyw 的 UI 层，业务层原样复用：
    剪贴板监听线程 + 工作线程（生产者-消费者）全部在 ui/bridge.py，
    UI 更新一律走 Qt Signal（等价原版的 root.after(0, ...)，跨线程自动排队）。

用法：
    python bili_yt_player_qml.py              # 正常启动
    python bili_yt_player_qml.py --autostart  # 开机自启：直接住托盘，不弹主窗口
"""

import sys

assert sys.version_info >= (3, 10), "需要 Python 3.10+"

import os
from pathlib import Path

# ---- PyInstaller --windowed 模式下 stdout/stderr 为 None，任何 print() 都会崩 ----
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
for _fh in (sys.stdout, sys.stderr):
    try:
        _fh.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# 无桌面（Linux 无 X / 远程会话）时给 Qt 一个明确的失败方式而不是静默卡住
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") \
        and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer, QUrl, Property, Signal, Slot  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP_NAME = "BiliYTPlayer"

# QML 目录定位：
#   源码运行 → 脚本同级 ui/
#   PyInstaller 打包 → sys._MEIPASS/ui（datas 里的 (ui, "ui") 会解到这里）
# 注意 legacy/ 与 app/ 走的是同一套逻辑（bridge.py 里也是这么算的）。
if getattr(sys, "frozen", False):
    _RUNTIME_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    _RUNTIME_DIR = Path(__file__).resolve().parent
UI_DIR = _RUNTIME_DIR / "ui"


def _panic_log(exc: BaseException) -> Path:
    """启动失败时把 traceback 落到配置目录（--windowed 下看不到控制台）。"""
    import traceback
    log_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{APP_NAME}_error.log"
    with open(log_path, "w", encoding="utf-8") as f:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
    return log_path


def _diag() -> int:
    """--diag：打印所有运行时路径，用于排查打包后文件找不到的问题。"""
    print("=" * 60)
    print("BiliYTPlayer 诊断")
    print("=" * 60)
    print(f"frozen       : {getattr(sys, 'frozen', False)}")
    print(f"sys.executable: {sys.executable}")
    print(f"_MEIPASS     : {getattr(sys, '_MEIPASS', '(none)')}")
    print(f"UI_DIR       : {UI_DIR}  exists={UI_DIR.is_dir()}")
    print(f"CONFIG_DIR   : {Path(os.environ.get('APPDATA', str(Path.home()))) / APP_NAME}")

    # 关键文件逐个查
    for rel in ("ui/Main.qml", "ui/qmldir", "ui/theme.qml", "ui/euikit/qmldir",
                "ui/settings/SettingsWindow.qml", "legacy/osc_base.lua"):
        p = _RUNTIME_DIR / rel
        print(f"  {rel:42s} exists={p.is_file()}")

    # 后端模块与 BASE_OSC_PATH
    for p in (str(_RUNTIME_DIR), str(_RUNTIME_DIR / "legacy")):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        import mpv_osc
        print(f"mpv_osc.__file__ : {mpv_osc.__file__}")
        print(f"BASE_OSC_PATH    : {mpv_osc.BASE_OSC_PATH}")
        print(f"  exists         : {mpv_osc.BASE_OSC_PATH.is_file()}")
    except Exception as e:
        import traceback
        print(f"import mpv_osc 失败: {e}")
        traceback.print_exc()

    try:
        from app import app_config
        print(f"app.app_config   : {app_config.__file__}")
    except Exception as e:
        print(f"import app.app_config 失败: {e}")

    print("=" * 60)
    return 0


def main() -> int:
    autostart_boot = "--autostart" in sys.argv

    # --diag：诊断模式，把关键路径打到 stdout 后退出。
    # 打包后 --windowed 没有控制台，排查路径问题（osc_base.lua / ui / legacy）
    # 时只能靠这个开关配合 `BiliYTPlayer.exe --diag > out.txt 2>&1`。
    if "--diag" in sys.argv:
        return _diag()

    # QQuickStyle 必须在 load() 之前设置（ui-kit/README.md:34-39）
    QQuickStyle.setStyle("FluentWinUI3")

    # 用 QApplication（不是 QGuiApplication）：托盘 QMenu 是 QWidget，
    # 没有 QApplication 时 QMenu() 会崩在 C++ 侧且无 Python 栈。
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("剪贴板直连播放器")
    app.setQuitOnLastWindowClosed(False)   # 最小化到托盘时窗口全隐藏也不能退出

    # ---- 单实例守卫（必须在创建 bridge / 起线程之前）----
    #
    # 本程序的核心是「剪贴板监听线程 + 新链接就开 mpv」。跑两份的后果不是
    # 弹两个一样的窗口那么轻：两个监听线程会各抓同一个链接，**同时开两个 mpv
    # 播同一个视频**，还各自写一遍播放历史。
    #
    # 放这里而不是 main() 最前面的原因：QLocalServer 依赖 QApplication 的
    # 事件循环，必须先有 app。但必须在 from ui.bridge import UiBridge 之前
    # 返回，否则 import 过程中就可能读到配置、起日志。
    from app.single_instance import SingleInstance
    from ui.bridge import CONFIG_DIR as _CFG_DIR_FOR_LOCK

    _single = SingleInstance(_CFG_DIR_FOR_LOCK)
    if _single.notify_existing():
        # 已经有实例在跑：先把它叫到前台，然后自己静默退出。
        # 不弹任何提示框 —— 用户只是想「打开程序」，直接把已有窗口给他就行。
        print("[i] BiliYTPlayer 已在运行，已唤回现有窗口。", flush=True)
        return 0
    if not _single.acquire():
        print("[!] 单实例锁获取失败（可能是权限问题），按多实例继续运行。", file=sys.stderr)
    app._dsh_single_instance = _single   # 保活 + 退出时 release

    # ---- 启动即应用已保存的深浅色（必须在 engine.load() 之前）----
    # Application.styleHints.colorScheme 单一开关同时驱动 Fluent 控件和 Theme.dark，
    # 晚于 load() 设置会让第一帧闪一下浅色再变深色，所以放在这里。
    def _apply_saved_theme():
        try:
            from app.app_config import load_config
            from app.theme import apply_theme
            from ui.bridge import CONFIG_DIR as _CFG
            return apply_theme(load_config(_CFG).get("theme", "system"))
        except Exception as e:
            print(f"[!] 主题应用失败（用系统默认）: {e}", file=sys.stderr)
            return "system"

    _theme_now = _apply_saved_theme()

    from ui.bridge import UiBridge

    bridge = UiBridge()
    # 必须让 bridge 的 PySide 包装对象活到 QML 引擎之后销毁。
    # setContextProperty 不接管所有权，若 bridge 只是个局部变量，解释器退出时
    # 会先回收它、再销毁 QML 引擎，于是所有 `bridge.xxx` 绑定在析构阶段报
    # "Cannot read property 'paused' of null"（实测，属于退出噪音但会淹掉真错误）。
    # 挂到 app 上让生命周期覆盖整个进程。
    app._dsh_bridge = bridge
    bridge._log(f"[*] 主题: {_theme_now}")

    engine = QQmlApplicationEngine()
    # 不要 addImportPath(UI_DIR)！
    #
    # 曾经加过这一行，后果很隐蔽：ui/qmldir 把 theme.qml 注册成单例，于是 ui/
    # 会被当成「模块」加载一次；而 ui/settings/*.qml 里又写 `import ".."`
    # 按「相对目录」解析，同一个目录被两套机制各加载一遍 → theme.qml 求值两次
    # → 它 import QtQuick.Controls，触发 FluentWinUI3 的 Config 单例重新初始化
    # → 所有 ComboBox 的 count/implicitHeight 归零，整个下拉框不画出来，
    #   而且一条 QML 警告都没有。实测对照：加这行 qualityCombo count=0，
    # 去掉后 count=11。
    #
    # 现在各文件都用相对 import（Main.qml 用 "euikit"/"."，settings 内部用
    # "../euikit" 与自己的 qmldir 单例），相对解析本身就够，不需要 import path。
    engine.rootContext().setContextProperty("bridge", bridge)

    # ---- 设置窗口（独立页面，settings-agent 交付）----
    settings_win = None
    settings_bridge = None
    try:
        from ui.settings_bridge import SettingsBridge
        settings_bridge = SettingsBridge(bridge.configDir(), log=bridge._log)
        # 同 bridge：保活到进程结束，避免析构阶段 QML 绑定读到 null
        app._dsh_settings_bridge = settings_bridge
        engine.rootContext().setContextProperty("settingsBridge", settings_bridge)

        # 设置页里改主题 → 立即作用到整个进程（含主窗口与设置窗口自身）。
        # apply_theme 只动 styleHints，Theme.dark 与 Fluent 控件会自己跟着刷新；
        # 这里补一次 config 重读，让主窗口的 general_cfg 也同步。
        def _on_theme_changed(theme: str):
            try:
                from app.theme import apply_theme
                applied = apply_theme(theme)
                bridge.general_cfg = dict(bridge.general_cfg, theme=applied)
                bridge._log(f"[*] 主题已切换: {applied}")
            except Exception as e:
                bridge._log(f"[!] 主题切换失败: {e}")

        settings_bridge.themeChanged.connect(_on_theme_changed)
    except Exception as e:
        bridge._log(f"[!] 设置模块加载失败（设置页不可用）: {e}")

    # ---- 关闭询问框 ----
    close_dlg = None
    try:
        engine.load(QUrl.fromLocalFile(str(UI_DIR / "CloseDialog.qml")))
        if engine.rootObjects():
            close_dlg = engine.rootObjects()[-1]
    except Exception as e:
        bridge._log(f"[!] 关闭询问框加载失败: {e}")

    # ---- 主窗口 ----
    # 关键：engine.rootObjects() 是所有已加载的根对象，CloseDialog 已经在里面了。
    # 必须记下加载前的数量再取新增的那个，否则会拿成 CloseDialog，
    # 后面 win.show() 就会把关闭询问框弹出来（实测踩过）。
    _n_before = len(engine.rootObjects())
    engine.load(QUrl.fromLocalFile(str(UI_DIR / "Main.qml")))
    _roots = engine.rootObjects()
    if len(_roots) <= _n_before:
        raise RuntimeError("Main.qml 加载失败（检查 ui/ 目录与 euikit 是否完整）")
    win = _roots[-1]
    assert win.property("title"), "Main.qml 根对象不是主窗口"

    if close_dlg is not None:
        try:
            close_dlg.setProperty("dialogParent", win)
        except Exception:
            pass
        win.setProperty("closeDialog", close_dlg)
        # 不要在 Python 侧 .connect 到 QML 的 function —— QML 的 function
        # 不是 Python 可调用属性。信号在 Main.qml 的 Connections 里接。

    # ---- 设置窗口：延迟加载，首次点「设置…」才创建 ----
    def _center_on(parent_win, child_win):
        """把 child 居中到 parent 上（两者都是 QQuickWindow，x/y 是屏幕坐标）。"""
        try:
            pg = parent_win.geometry()
            cg = child_win.geometry()
            child_win.setPosition(
                pg.x() + max(0, (pg.width() - cg.width()) // 2),
                pg.y() + max(0, (pg.height() - cg.height()) // 2),
            )
        except Exception:
            pass

    def open_settings(page: str = ""):
        nonlocal settings_win
        if settings_bridge is None:
            bridge.toast.emit("设置模块不可用")
            return
        if settings_win is None:
            engine.load(QUrl.fromLocalFile(str(UI_DIR / "settings" / "SettingsWindow.qml")))
            roots = engine.rootObjects()
            if not roots:
                bridge.toast.emit("设置页加载失败")
                return
            settings_win = roots[-1]
            try:
                settings_bridge.configChanged.connect(bridge.reloadConfigs)
            except Exception:
                pass

        if page:
            settings_win.setProperty("initialPage", page)
        # QML 里公开的 function open() 从 Python 调不到，用 visible 属性即可
        settings_win.setProperty("visible", True)
        try:
            settings_win.raise_()
            settings_win.requestActivate()
        except Exception:
            pass
        _center_on(win, settings_win)

    bridge.openSettingsRequested.connect(open_settings)

    # ---- 系统托盘 ----
    # 托盘信号一律转成 bridge 的信号，由 Main.qml 的 Connections 处理。
    # 不直接调 QML 函数，也不直接调 win.show() —— 让 QML 侧统一管窗口状态。
    tray = None
    try:
        from app.tray import Tray
        tray = Tray()
        if tray.available:
            tray.show()
            tray.showRequested.connect(bridge.trayShowRequested)
            tray.settingsRequested.connect(bridge.traySettingsRequested)
            tray.quitRequested.connect(bridge.trayQuitRequested)
            win.setProperty("tray", tray)
            tray.setTip("BiliYTPlayer — 复制链接即可播放")
        else:
            tray = None
            bridge._log("[*] 系统托盘不可用，关闭窗口将直接退出")
    except Exception as e:
        tray = None
        bridge._log(f"[!] 托盘初始化失败: {e}")

    # ---- 自启启动：不弹主窗口，只住托盘 ----
    if autostart_boot:
        bridge.minimizeToTrayRequested.emit()
        if tray:
            tray.notify(APP_NAME, "已在后台监听剪贴板，复制链接即可播放")
    else:
        win.show()

    # ---- 又点了一次程序图标：把已有窗口叫回来（而不是开第二份）----
    # 复用 QML 侧已有的 trayShowRequested 处理（show + raise + requestActivate）。
    # 若之前是最小化到托盘状态，用户体感就是「图标点一下就回来了」。
    _single.activated.connect(bridge.trayShowRequested)

    def _cleanup_on_quit():
        """退出前释放单实例锁，否则下次启动要等管道超时。"""
        try:
            _single.release()
        except Exception:
            pass

    app.aboutToQuit.connect(_cleanup_on_quit)

    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:      # noqa: BLE001 — 顶层兜底，必须落盘
        try:
            p = _panic_log(e)
            print(f"[!] 启动失败，日志: {p}", file=sys.stderr)
        except Exception:
            pass
        raise
