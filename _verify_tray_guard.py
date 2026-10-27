# -*- coding: utf-8 -*-
"""Tray 防御分支自检：只建 QGuiApplication（没有 QApplication）时，
Tray 必须退化成静默空壳而不是崩。

单独成文件的原因：QApplication 一旦建起来就没法退回 QGuiApplication，
必须在新进程里测。由 _verify_shell.py 调用。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

app = QGuiApplication([])
# 前置条件：instance() 不是 None，但确实不是 QApplication
assert QApplication.instance() is not None, "前置条件不成立"
assert not isinstance(QApplication.instance(), QApplication), "前置条件不成立"

from app.tray import Tray

t = Tray()
assert t._icon is None, "_icon 应为 None（未走 QWidget 构造）"
assert t._menu is None, "_menu 应为 None"
assert t.menu_actions == [], f"menu_actions 应为空: {t.menu_actions}"
# 无托盘环境的 no-op 方法全部不能抛
t.show()
t.hide()
t.setTip("测试")
t.notify("标题", "内容")
print("GUARD OK")
sys.exit(0)
