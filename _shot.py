# -*- coding: utf-8 -*-
"""QML 离屏截图验证工具（开发期用，不进发行包）。

用法:
    python _shot.py <qml路径> <输出png> [宽] [高] [light|dark]

环境: offscreen 平台 + software 后端，中文字体指向 C:\\Windows\\Fonts。
截图对 Fluent 控件同样有效（FluentWinUI3 样式在离屏下正常渲染）。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

qml = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
W = int(sys.argv[3]) if len(sys.argv) > 3 else 980
H = int(sys.argv[4]) if len(sys.argv) > 4 else 720
scheme = sys.argv[5] if len(sys.argv) > 5 else ""

QQuickStyle.setStyle("FluentWinUI3")
app = QGuiApplication(sys.argv)
engine = QQmlApplicationEngine()
engine.addImportPath(str(qml.parent))
engine.rootContext().setContextProperty("__forceScheme", scheme)
engine.load(QUrl.fromLocalFile(str(qml)))
if not engine.rootObjects():
    print("LOAD FAIL", qml)
    sys.exit(1)
win = engine.rootObjects()[0]
win.resize(W, H)
win.show()


def grab():
    img = win.grabWindow()
    print(("saved " if img.save(str(out)) else "SAVE FAIL ") + str(out),
          img.width(), "x", img.height())
    app.quit()


QTimer.singleShot(1400, grab)
app.exec()
