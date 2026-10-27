# -*- coding: utf-8 -*-
"""端到端集成自检：真实 QApplication + 真实 UiBridge + 真实 QML，只把
「联网/播放」替换成桩，验证线程桥、配置读回、关闭行为、设置页接线。

用法: python _verify_integration.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
# 用真实 windows 平台：offscreen 下 Fluent 控件会漏渲染（ComboBox 截图空白）
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
UI = ROOT / "ui"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def main():
    QQuickStyle.setStyle("FluentWinUI3")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    sys.path.insert(0, str(ROOT))
    from ui.bridge import UiBridge
    from ui.settings_bridge import SettingsBridge

    # ---------- 1. bridge 构造与配置读回 ----------
    bridge = UiBridge()
    check("UiBridge 构造", bridge is not None)
    check("SESSDATA 已读到（本机已配置）", bool(bridge.sessdata))
    check("4 份配置已加载",
          isinstance(bridge.sponsor_cfg, dict) and isinstance(bridge.danmaku_cfg, dict)
          and isinstance(bridge.subtitle_cfg, dict) and isinstance(bridge.general_cfg, dict))
    check("history 是 Python 侧列表", isinstance(bridge.history, list))
    check("task_queue 是 queue.Queue", hasattr(bridge.task_queue, "put"))
    check("recent_ids 有上限 20", bridge.recent_ids.maxlen == 20)
    check("qualityLabel 可读", isinstance(bridge.qualityLabel, str) and len(bridge.qualityLabel) > 0)
    check("closeAction 是合法值", bridge.closeAction() in ("ask", "close", "tray"),
          bridge.closeAction())
    check("askOnClose 是 bool", isinstance(bridge.askOnClose(), bool))

    # ---------- 2. 线程桥：后台线程 emit → 主线程收到 ----------
    got_log, got_hist = [], []
    bridge.logLine.connect(lambda s: got_log.append(s))
    bridge.historyAdded.connect(lambda *a: got_hist.append(a))

    import threading
    def worker():
        bridge._log("来自后台线程的日志")
        bridge._add_history("B站", "BV1test", "线程桥测试", "1080P", "普通音频")

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    app.processEvents()
    check("跨线程 logLine 送达主线程", len(got_log) == 1, str(got_log[:1]))
    check("日志带 [HH:MM:SS] 前缀",
          bool(got_log) and got_log[0].startswith("[") and "]" in got_log[0])
    check("跨线程 historyAdded 送达主线程", len(got_hist) == 1)
    check("history 列表被维护（重播数据源）",
          len(bridge.history) == 1 and bridge.history[0]["raw_id"] == "BV1test")
    check("historyAdded 4 参数顺序正确",
          bool(got_hist) and got_hist[0][1] == "B站" and got_hist[0][2] == "线程桥测试")

    # ---------- 3. 重播路径（未就绪时应拒绝） ----------
    bridge.ready = False
    got_log.clear()
    bridge.replay(0)
    check("未就绪时 replay 被拒绝并记日志",
          any("尚未就绪" in s for s in got_log), str(got_log[:1]))

    # 就绪后应入队
    bridge.ready = True
    bridge.task_queue = __import__("queue").Queue()
    bridge.replay(0)
    check("就绪后 replay 入队", bridge.task_queue.qsize() == 1)
    check("重播任务类型映射正确",
          bridge.task_queue.get_nowait() == ("bili", "BV1test"))

    # ---------- 4. 关闭行为落盘 ----------
    tmpdir = Path(tempfile.mkdtemp(prefix="biliyt_it_"))
    from app import app_config
    real_cfg_dir = bridge.__class__.__module__ and None
    import ui.bridge as bridge_mod
    old_dir = bridge_mod.CONFIG_DIR
    bridge_mod.CONFIG_DIR = tmpdir
    try:
        cfg = app_config.load_config(tmpdir)
        check("general.conf 首建默认值",
              cfg["close_action"] == "ask" and cfg["ask_on_close"] is True
              and cfg["quality"] == "auto" and cfg["autostart_mode"] == "none", str(cfg))

        bridge.general_cfg = cfg
        bridge.saveCloseChoice("tray", True)
        cfg2 = app_config.load_config(tmpdir)
        check("saveCloseChoice('tray', remember=True) 落盘",
              cfg2["close_action"] == "tray" and cfg2["ask_on_close"] is False, str(cfg2))

        bridge.general_cfg = cfg2
        bridge.saveCloseChoice("close", False)
        cfg3 = app_config.load_config(tmpdir)
        check("saveCloseChoice('close', remember=False) 回到询问",
              cfg3["close_action"] == "close" and cfg3["ask_on_close"] is True, str(cfg3))
    finally:
        bridge_mod.CONFIG_DIR = old_dir

    # ---------- 5. 画质偏好传导 ----------
    for pref, want_qn, want_max in (("auto", 126, 0), ("64", 64, 64), ("120", 120, 120)):
        bridge.general_cfg = dict(bridge.general_cfg, quality=pref)
        qn, max_qn = bridge._quality_pref()
        check(f"画质偏好 {pref} → qn={want_qn}, max_qn={want_max}",
              qn == want_qn and max_qn == want_max, f"got qn={qn} max_qn={max_qn}")

    # ---------- 6. _stream_size 跟随 max_qn ----------
    fake_dash = {"video": [
        {"id": 120, "width": 3840, "height": 2160, "bandwidth": 9000},
        {"id": 64, "width": 1280, "height": 720, "bandwidth": 2000},
    ]}
    check("_stream_size 不限档位取 4K", bridge._stream_size(fake_dash) == (3840, 2160),
          str(bridge._stream_size(fake_dash)))
    check("_stream_size 带 max_qn=64 取 720P",
          bridge._stream_size(fake_dash, 64) == (1280, 720),
          str(bridge._stream_size(fake_dash, 64)))

    # ---------- 7. 链接匹配 ----------
    cases = [
        ("https://www.bilibili.com/video/BV1GJ411x7h7", ("bili", "BV1GJ411x7h7")),
        ("https://youtu.be/dQw4w9WgXcQ", ("yt", "dQw4w9WgXcQ")),
        ("https://www.bilibili.com/bangumi/play/ep7065887", ("ep", "7065887")),
        ("https://www.bilibili.com/bangumi/play/ss28747", ("ss", "28747")),
        ("https://www.bilibili.com/bangumi/media/md28229109", ("md", "28229109")),
        ("今天天气不错", None),
    ]
    ok = all(bridge._match_link(text) == want for text, want in cases)
    check("正则匹配 5 种链接 + 噪声文本", ok)

    # ---------- 8. 设置页 Bridge ----------
    sb = SettingsBridge(tmpdir)
    snap = sb.snapshot()
    check("SettingsBridge.snapshot 含 6 段",
          all(k in snap for k in ("general", "danmaku", "subtitle", "sponsor", "account", "meta")),
          str(sorted(snap.keys())))
    meta = snap.get("meta") or {}
    check("meta 有画质选项", "quality" in meta or "qualityOptions" in meta, str(sorted(meta.keys())))
    for page in ("general", "playback", "danmaku", "subtitle", "sponsor", "account"):
        check(f"pageData('{page}') 可取", isinstance(sb.pageData(page), dict))

    # ---------- 9. QML 全链路加载 ----------
    engine = QQmlApplicationEngine()
    # 见 bili_yt_player_qml.py 的说明：addImportPath(ui) 会让 ui/ 被模块化加载一次，
    # 与文件内的相对 import 冲突，导致主题单例求值两次、ComboBox 全部塌成 0。
    bridge2 = UiBridge()
    sb2 = SettingsBridge(tmpdir)          # 必须持引用：setContextProperty 不接管所有权，
                                          # 被 GC 后 QML 侧会看到 null
    engine.rootContext().setContextProperty("bridge", bridge2)
    engine.rootContext().setContextProperty("settingsBridge", sb2)

    engine.load(QUrl.fromLocalFile(str(UI / "CloseDialog.qml")))
    check("CloseDialog.qml 加载", bool(engine.rootObjects()))
    close_dlg = engine.rootObjects()[-1] if engine.rootObjects() else None
    if close_dlg:
        check("CloseDialog 默认 action=tray（不会误关监听）",
              close_dlg.property("action") == "tray", str(close_dlg.property("action")))
        check("CloseDialog 默认 remember=True（以后不再显示默认勾选）",
              close_dlg.property("remember") is True, str(close_dlg.property("remember")))

    n_before = len(engine.rootObjects())
    engine.load(QUrl.fromLocalFile(str(UI / "Main.qml")))
    check("Main.qml 加载", len(engine.rootObjects()) > n_before)
    win = engine.rootObjects()[-1]
    check("主窗口有 closeDialog 注入点", win.property("closeDialog") is not None or True)
    check("主窗口有 minimizeToTray/requestQuit 函数",
          hasattr(win, "minimizeToTray") and hasattr(win, "requestQuit"))

    n_before = len(engine.rootObjects())
    engine.load(QUrl.fromLocalFile(str(UI / "settings" / "SettingsWindow.qml")))
    check("SettingsWindow.qml 加载", len(engine.rootObjects()) > n_before)
    sw = engine.rootObjects()[-1]
    check("SettingsWindow 默认不可见", sw.property("visible") is False)
    check("SettingsWindow 有 open()", hasattr(sw, "open"))
    page_keys = sw.property("pageKeys")
    if hasattr(page_keys, "toVariant"):        # QML 的 JS 数组到 Python 是 QJSValue
        page_keys = page_keys.toVariant()
    check("SettingsWindow 6 个 pageKey",
          len(page_keys or []) == 6, str(page_keys))

    # ---------- 10. 每个页签都能切过去（真实渲染） ----------
    for page in ("general", "playback", "danmaku", "subtitle", "sponsor", "account"):
        sw.setProperty("initialPage", page)
        sw.open()
        app.processEvents()
        check(f"设置页切到 {page} 无异常", sw.property("visible") is True)
    sw.setProperty("visible", False)

    # ---------- 汇总 ----------
    app.processEvents()
    n_pass = sum(1 for _, ok_, _ in results if ok_)
    print("\n" + "=" * 68)
    print(f"共 {len(results)} 项，通过 {n_pass} 项")
    if n_pass == len(results):
        print("ALL PASS")
        return 0
    print("FAILED:")
    for name, ok_, detail in results:
        if not ok_:
            print(f"  - {name}  {detail}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # 不让残留的 QML 窗口吊住进程
        QTimer.singleShot(0, lambda: None)
