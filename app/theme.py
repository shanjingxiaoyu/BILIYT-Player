# -*- coding: utf-8 -*-
"""深浅色主题的应用 —— 把 ui.conf/general.conf 里的 `theme` 落到 Qt 上。

为什么只需要这一处：`Application.styleHints.colorScheme` 是**唯一开关**，
它同时驱动
    * Fluent 控件自身的配色（QtQuick.Controls 内部读它）
    * `ui/theme.qml` 的 `Theme.dark`（我们自己 token 的深/浅分支）
所以设一次两边都对，不需要在每个 QML 里传色。

三个取值：
    system  → Qt.ColorScheme.Unknown，交回系统决定（Windows 跟随"应用模式"设置）
    light   → 强制浅色
    dark    → 强制深色

Qt 6.9 的 setColorScheme 在 Windows/macOS/Linux 都可用；不过某些平台会忽略它，
所以任何异常都静默降级为跟随系统 —— 主题是外观，不能成为启动失败的原因。
"""

from PySide6.QtCore import Qt

#: 合法取值与中文标签（设置页下拉/单选直接用这个顺序）
THEME_OPTIONS = (
    ("dark", "深色模式"),
    ("light", "浅色模式"),
    ("system", "跟随系统"),
)

#: 别名 → 规范值。老配置里可能写 true/false 之类，一律容错
_ALIASES = {
    "dark": "dark", "深色": "dark",
    "light": "light", "浅色": "light",
    "system": "system", "auto": "system", "跟随系统": "system", "": "system",
}

_QT_SCHEME = {
    "dark": Qt.ColorScheme.Dark,
    "light": Qt.ColorScheme.Light,
    "system": Qt.ColorScheme.Unknown,
}


def normalize(name) -> str:
    """任意脏值 → 合法 theme 名（默认 system）。"""
    return _ALIASES.get(str(name or "").strip().lower(), "system")


def apply_theme(name):
    """把主题应用到 Qt。返回实际生效的规范值。

    必须在 QGuiApplication 建好之后调用；建好之前调会静默失败。
    """
    key = normalize(name)
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is None:
            return key
        app.styleHints().setColorScheme(_QT_SCHEME[key])
    except Exception:
        # 平台不支持 / 没有 GUI：保持跟随系统，不影响运行
        return "system"
    return key
