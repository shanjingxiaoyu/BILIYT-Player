# -*- coding: utf-8 -*-
"""验证「深浅色跟随系统」真的成立（这是 MIGRATION_PROMPT 的验收项）。

要点：Application.styleHints.colorScheme 必须是**可绑定属性**且变化时发信号，
否则 Theme.dark 是死的，切系统主题不刷新就等于没实现。
"""
import os
import sys
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

    hints = app.styleHints()

    # ---- 1. colorScheme 是可比对、可读的 ----
    check("styleHints.colorScheme 可读", hints is not None)
    scheme = hints.colorScheme()
    check("colorScheme 是合法枚举值",
          scheme in (getattr(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt, "ColorScheme").Unknown,
                     getattr(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt, "ColorScheme").Light,
                     getattr(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt, "ColorScheme").Dark),
          str(scheme))

    # ---- 2. 有 colorSchemeChanged 信号（Theme.dark 的绑定靠它刷新）----
    has_sig = hasattr(hints, "colorSchemeChanged")
    check("有 colorSchemeChanged 信号（绑定才会刷新）", has_sig)

    # ---- 3. 该信号真的会 emit（改 colorScheme 触发）----
    from PySide6.QtCore import Qt
    fired = []
    if has_sig:
        hints.colorSchemeChanged.connect(lambda: fired.append(hints.colorScheme()))

    try:
        hints.setColorScheme(Qt.ColorScheme.Dark)
        app.processEvents()
        dark_fired = len(fired) > 0 and hints.colorScheme() == Qt.ColorScheme.Dark
        check("可切到 Dark 且信号触发", dark_fired, f"fired={len(fired)} cs={hints.colorScheme()}")

        if dark_fired:
            # Theme.dark 读的就是它，所以 Theme.dark 也会变
            n_before = len(fired)
            hints.setColorScheme(Qt.ColorScheme.Light)
            app.processEvents()
            check("可切到 Light 且信号再次触发",
                  len(fired) > n_before and hints.colorScheme() == Qt.ColorScheme.Light,
                  f"fired={len(fired)} cs={hints.colorScheme()}")

            # 恢复跟随系统
            hints.setColorScheme(Qt.ColorScheme.Unknown)
            app.processEvents()
            check("Unknown = 恢复跟随系统", hints.colorScheme() != Qt.ColorScheme.Dark, 
                  str(hints.colorScheme()))
    except Exception as e:
        print(f"    [!] 平台不支持写 colorScheme: {e}（Linux/部分平台正常现象）")

    # ---- 4. Theme.dark 在 QML 里真的跟着变 ----
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(ROOT / "ui"))

    from PySide6.QtQml import QQmlComponent
    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(ROOT / "ui" / "theme.qml")))
    # theme.qml 是 singleton，不能直接实例化；改为加载一个读 Theme.dark 的小 QML
    probe = ROOT / "_scratch" / "theme_probe.qml"
    probe.write_text(
        'import QtQuick\n'
        'import ".."\n'
        'QtObject { property bool isDark: Theme.dark; '
        'property color cardColor: Theme.card }\n',
        encoding="utf-8",
    )
    # 放到 ui/ 下才能 import ".."
    target = ROOT / "ui" / "_theme_probe.qml"
    target.write_text(
        'import QtQuick\n'
        'import "."\n'
        'QtObject { property bool isDark: Theme.dark; '
        'property color cardColor: Theme.card }\n',
        encoding="utf-8",
    )
    try:
        engine.load(QUrl.fromLocalFile(str(target)))
        obj = engine.rootObjects()[-1]
        try:
            hints.setColorScheme(Qt.ColorScheme.Dark)
            app.processEvents()
            dark_val = obj.property("isDark")
            dark_card = obj.property("cardColor")
            hints.setColorScheme(Qt.ColorScheme.Light)
            app.processEvents()
            light_val = obj.property("isDark")
            light_card = obj.property("cardColor")
            check("Theme.dark 跟随 colorScheme 变为 True", dark_val is True, str(dark_val))
            check("Theme.dark 跟随 colorScheme 变为 False", light_val is False, str(light_val))
            check("Theme.card 随深浅色换色（不是死绑定）",
                  str(dark_card) != str(light_card), f"{dark_card} vs {light_card}")
        finally:
            hints.setColorScheme(Qt.ColorScheme.Unknown)
    finally:
        try:
            target.unlink()
        except OSError:
            pass

    n = sum(1 for _, ok_, _ in results if ok_)
    print("\n" + "=" * 66)
    print(f"共 {len(results)} 项，通过 {n} 项")
    print("ALL PASS" if n == len(results) else "FAILED")
    return 0 if n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
