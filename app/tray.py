# -*- coding: utf-8 -*-
"""系统托盘 —— QSystemTrayIcon + 现画图标，不依赖任何外部图片文件。

用法（**只能在 QApplication 之后**构造，且在事件循环里才显示）::

    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    tray = Tray()
    tray.showRequested.connect(win.show)
    tray.quitRequested.connect(app.quit)
    tray.show()

`QSystemTrayIcon` 内部会建 QWidget：没有 QApplication 直接崩
（QWidget: Cannot create a QWidget without QApplication），
所以构造时用 QApplication.instance() 做防御检查，缺失就退化成静默空壳。

无托盘环境（远程会话 / 部分 Linux 桌面）下 available 为 False，
show/hide/setTip/notify 全部静默 no-op，不抛异常。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


def _make_pixmap(size: int) -> QPixmap:
    """现画图标：深色圆角方块 + 白色播放三角 + B站蓝描边。

    16/32 各画一份，避免从大图缩小时糊成一团；三角用白色而不是蓝色，
    因为托盘尺寸下白/深对比远比蓝/深可靠（浅色、深色任务栏都认得出）。
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    # 底：深色圆角方块，留一点内边距给抗锯齿
    inset = max(1.0, size * 0.05)
    box = QPainterPath()
    box.addRoundedRect(inset, inset, size - inset * 2, size - inset * 2,
                       size * 0.24, size * 0.24)
    p.fillPath(box, QColor("#1B1B1B"))

    # B站蓝描边（16px 下只留 1px，否则会吃掉深色面积）
    pen = QPen(QColor("#00AEEC"))
    pen.setWidthF(max(1.0, size / 32.0))
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(box)

    # 白色播放三角
    w, h = size * 0.40, size * 0.44
    cx, cy = size / 2.0, size / 2.0
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    p.drawPolygon(QPolygonF([
        QPointF(cx - w * 0.38, cy - h / 2),
        QPointF(cx - w * 0.38, cy + h / 2),
        QPointF(cx + w * 0.62, cy),
    ]))
    p.end()
    return pm


class Tray(QObject):
    """托盘图标 + 右键菜单（显示主窗口 / 设置 / 退出）。"""

    showRequested = Signal()      # 双击图标 或 菜单"显示主窗口"
    settingsRequested = Signal()  # 菜单"设置"
    quitRequested = Signal()      # 菜单"退出"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._icon: QSystemTrayIcon | None = None
        self._menu: QMenu | None = None
        self._tip = "BiliYTPlayer"

        # 防御：QSystemTrayIcon / QMenu 都是 QWidget 体系的，需要有 QApplication。
        # 注意不能写 `QApplication.instance() is None` —— 只建了 QGuiApplication 时
        # instance() 返回的是那个 QGuiApplication 实例（不是 None），判断会漏，
        # 随后 QMenu() 直接 "QWidget: Cannot create a QWidget without QApplication" 崩掉。
        if not isinstance(QApplication.instance(), QApplication):
            return

        self._menu = QMenu()
        act_show = self._menu.addAction("显示主窗口")
        act_settings = self._menu.addAction("设置")
        act_quit = self._menu.addAction("退出")
        act_show.triggered.connect(self.showRequested)
        act_settings.triggered.connect(self.settingsRequested)
        act_quit.triggered.connect(self.quitRequested)

        icon = QIcon()
        for s in (16, 32):
            icon.addPixmap(_make_pixmap(s))

        self._icon = QSystemTrayIcon(self)
        self._icon.setIcon(icon)
        self._icon.setToolTip(self._tip)
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)

    # ---- 内部 ----
    def _on_activated(self, reason) -> None:
        # 只认双击：Windows 单击也给 Trigger，认它会导致双击 emit 两次
        if reason == QSystemTrayIcon.DoubleClick:
            self.showRequested.emit()

    # ---- 属性 ----
    @property
    def available(self) -> bool:
        """当前环境是否有系统托盘。"""
        return QSystemTrayIcon.isSystemTrayAvailable()

    @property
    def menu_actions(self) -> list:
        """菜单项列表（自检用：应恰好 3 项）。"""
        return self._menu.actions() if self._menu is not None else []

    # ---- 对外 ----
    # 必须加 @Slot：QML 只能调用注册到元对象系统的方法（信号槽 / Q_INVOKABLE /
    # @Slot）。裸 def 在 Python 侧能调，但 QML 侧会报
    # "Property 'hide' of object Tray(...) is not a function"（实测）。
    @Slot()
    def show(self) -> None:
        """显示托盘图标。无托盘 / 无 QApplication 时静默返回。"""
        if self._icon is None or not self.available:
            return
        self._icon.show()

    @Slot()
    def hide(self) -> None:
        if self._icon is None:
            return
        self._icon.hide()

    @Slot(str)
    def setTip(self, text: str) -> None:
        self._tip = text or "BiliYTPlayer"
        if self._icon is not None:
            self._icon.setToolTip(self._tip)

    @Slot(str, str)
    @Slot(str, str, int)
    def notify(self, title: str, body: str, msecs: int = 3000) -> None:
        """气泡提示；无托盘时不弹（调用也不报错）。"""
        if self._icon is None or not self.available:
            return
        self._icon.showMessage(title, body, QSystemTrayIcon.Information, msecs)
