# -*- coding: utf-8 -*-
"""带 mock bridge 的主窗口渲染自检：真实 QML + 桩 bridge，离屏截图。

用法: python _shot_main.py [out.png] [light|dark]
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import Property, QObject, QUrl, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle


class MockBridge(QObject):
    """形状与 ui/bridge.py 的 UiBridge 一致，但不联网、不读磁盘。"""

    logLine = Signal(str)
    historyAdded = Signal(str, str, str, str)
    historyCleared = Signal()
    pausedChanged = Signal()
    readyChanged = Signal()
    toast = Signal(str)
    openSettingsRequested = Signal(str)
    firstRunPrompt = Signal()
    skipChanged = Signal()
    danmakuChanged = Signal()
    subtitleChanged = Signal()

    def __init__(self):
        super().__init__()
        self._paused = False
        self._ready = True
        self._skip = True
        self._dm = True
        self._cc = False

    def _get_paused(self): return self._paused
    def _set_paused(self, v): self._paused = bool(v); self.pausedChanged.emit()
    paused = Property(bool, _get_paused, _set_paused, notify=pausedChanged)

    def _get_ready(self): return self._ready
    def _set_ready(self, v): self._ready = bool(v); self.readyChanged.emit()
    ready = Property(bool, _get_ready, _set_ready, notify=readyChanged)

    def _get_skip(self): return self._skip
    def _set_skip(self, v): self._skip = bool(v); self.skipChanged.emit()
    skipEnabled = Property(bool, _get_skip, _set_skip, notify=skipChanged)

    def _get_dm(self): return self._dm
    def _set_dm(self, v): self._dm = bool(v); self.danmakuChanged.emit()
    danmakuEnabled = Property(bool, _get_dm, _set_dm, notify=danmakuChanged)

    def _get_cc(self): return self._cc
    def _set_cc(self, v): self._cc = bool(v); self.subtitleChanged.emit()
    subtitleEnabled = Property(bool, _get_cc, _set_cc, notify=subtitleChanged)

    @Property(str, constant=True)
    def qualityLabel(self): return "自动（最高可用）"

    @Slot()
    def bootstrap(self):
        self.logLine.emit("[17:20:01] 正在初始化…")
        self.logLine.emit("[17:20:01] [*] 检测播放器…")
        self.logLine.emit("[17:20:02] [+] 播放器: E:\\...\\mpv-portable\\mpv.exe")
        self.logLine.emit("[17:20:02] [*] B 站鉴权… 连接B站 API")
        self.logLine.emit("[17:20:03] [+] B 站鉴权就绪。")
        self.logLine.emit("[17:20:03] [*] 监听中 — 复制 B 站 / YouTube 链接即可播放。")
        self.logLine.emit("[17:22:11] >> B 站: BV1GJ411x7h7")
        self.logLine.emit("[17:22:12]   [+] 画质: 1080P60 1920x1080 avc1.640032")
        self.logLine.emit("[17:22:12]   [*] 弹幕: 3189 条")
        self.logLine.emit("[17:22:12]   [*] SponsorBlock: 命中 跳过 2 段 0m45s")
        self.logLine.emit("[17:22:13] [+] mpv 正在播放: 【官方 MV】Never Gonna Give You Up")
        for row in (
            ("12:01", "B站", "【示例】视频标题一", "1080P / 192k"),
            ("12:02", "YouTube", "Example Video Two", "2160P / 320k"),
            ("12:03", "B站", "【示例】番剧 第 3 话", "1080P / 192k"),
            ("12:07", "B站", "【示例】视频标题四", "720P / 128k"),
            ("12:11", "B站", "【示例】很长的视频标题用于测试省略号是否正确工作", "1080P / 192k"),
        ):
            self.historyAdded.emit(*row)

    @Slot()
    def togglePause(self):
        self._set_paused(not self._paused)

    @Slot()
    def clearHistory(self):
        self.historyCleared.emit()

    @Slot(int)
    def replay(self, idx):
        self.logLine.emit(f"[17:23:00] >> 重播历史: #{idx}")

    @Slot(str)
    def openSettings(self, page):
        print("openSettings", repr(page))

    @Slot(str, bool)
    def saveCloseChoice(self, action, remember):
        print("saveCloseChoice", action, remember)

    @Slot(result=str)
    def closeAction(self): return "ask"

    @Slot(result=bool)
    def askOnClose(self): return True

    def shutdown(self): pass


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "_main_light.png").resolve()
    scheme = sys.argv[2] if len(sys.argv) > 2 else "light"
    ui = Path(__file__).resolve().parent / "ui"
    qml = ui / "Main.qml"

    QQuickStyle.setStyle("FluentWinUI3")
    app = QGuiApplication(sys.argv[:1])
    engine = QQmlApplicationEngine()
    # 见 bili_yt_player_qml.py 的说明：addImportPath(ui) 会让 ui/ 被模块化加载一次，
    # 与文件内的相对 import 冲突，导致主题单例求值两次、ComboBox 全部塌成 0。
    mock = MockBridge()          # 必须持引用：setContextProperty 不接管所有权
    engine.rootContext().setContextProperty("bridge", mock)
    engine.rootContext().setContextProperty("__forceScheme", scheme)
    print("loading", qml, "scheme", scheme, flush=True)
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        print("LOAD FAIL")
        return 1
    win = engine.rootObjects()[0]
    win.resize(720, 660)
    win.show()
    print("shown", flush=True)

    def grab():
        print("grabbing", flush=True)
        img = win.grabWindow()
        print(("saved " if img.save(str(out)) else "SAVE FAIL ") + str(out),
              img.width(), "x", img.height(), flush=True)
        app.quit()

    QTimer.singleShot(1200, grab)
    QTimer.singleShot(6000, app.quit)   # 兜底：任何情况下不挂住
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
