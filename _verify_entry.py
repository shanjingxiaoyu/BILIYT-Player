# -*- coding: utf-8 -*-
"""真实启动自检：跑 bili_yt_player_qml.py 的 main()，9 秒后截图并退出。

验证「真实入口 + 真实 windows 平台 + 真实 bridge + 真实托盘」能起来，
不是加载单个 QML 文件的近似。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer                      # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

import bili_yt_player_qml as entry                     # noqa: E402

_orig_exec = QApplication.exec


def _title(w):
    t = w.title()
    return t if isinstance(t, str) else ""


def patched_exec(self=None):
    app = QApplication.instance()

    def grab_and_quit():
        wins = [w for w in app.topLevelWindows() if w.isVisible()]
        print(f"[i] 可见顶层窗口 {len(wins)} 个")
        for w in wins:
            print(f"    - {_title(w)!r}  {w.width()}x{w.height()}")
        target = next((w for w in wins if "剪贴板" in _title(w)), wins[0] if wins else None)
        if target is not None:
            img = target.grabWindow()
            ok = img.save(str(ROOT / "_entry_shot.png"))
            print(f"[i] 主窗口 {target.width()}x{target.height()} "
                  f"截图 {'成功' if ok else '失败'}  标题={_title(target)!r}")
        app.quit()

    QTimer.singleShot(9000, grab_and_quit)
    QTimer.singleShot(30000, app.quit)      # 兜底
    return _orig_exec()


QApplication.exec = patched_exec
rc = entry.main()
print("[i] main() 返回", rc)
