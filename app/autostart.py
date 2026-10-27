# -*- coding: utf-8 -*-
"""开机自启 —— 只写当前用户注册表 Run 项，不需要管理员权限。

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
        BiliYTPlayer = "C:\\path\\BiliYTPlayer.exe" --autostart

`--autostart` 由入口脚本识别（启动后直接住进托盘，不弹主窗口）。

所有函数**绝不抛异常**：失败一律返回 (False, 人类可读原因)，设置页直接把字符串显示出来。
非 Windows 平台 options() 仍返回完整选项，apply() 返回 (False, "仅 Windows 支持")。
"""

from __future__ import annotations

import sys

# 注册表项名，固定不变（避免升级后残留两个自启项）
APP_NAME = "BiliYTPlayer"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# 自启时带的命令行参数，见模块 docstring
AUTOSTART_ARG = "--autostart"

# 非 Windows 下 winreg 不存在，用 None 表示"平台不支持"
try:
    import winreg
except ImportError:  # pragma: no cover - 仅非 Windows
    winreg = None


def options() -> tuple[tuple[str, str], ...]:
    """设置页下拉框的全部选项：((值, 显示文字), ...)。"""
    return (("none", "仅手动启动"), ("registry", "开机自启"))


def current_exe() -> str | None:
    """当前可执行文件路径；源码运行时返回 None（此时自启没有意义）。"""
    if not getattr(sys, "frozen", False):
        return None
    exe = (sys.executable or "").strip()
    return exe or None


def _command(exe_path: str) -> str:
    """注册表值：路径加引号防空格，后缀 --autostart。"""
    return f'"{exe_path}" {AUTOSTART_ARG}'


def _stored_exe(value: str) -> str:
    """从注册表值里剥出 exe 路径，用于和 current_exe() 比对。"""
    value = (value or "").strip()
    if value.startswith('"'):
        end = value.find('"', 1)
        return value[1:end] if end > 0 else value.strip('"')
    # 未加引号的老值：取到 " --autostart" 或第一个空格为止
    return value.split(" " + AUTOSTART_ARG)[0].strip()


def _read() -> str | None:
    """读注册表里的当前值；不存在返回 None。"""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return str(value)
    except OSError:
        return None


def is_enabled(mode: str) -> bool:
    """registry 模式下：项存在且指向当前 exe 才算启用。

    源码运行时 current_exe() 为 None，无从比对，只要项存在就认为启用
    （用户可能就是手动写进去的，不该谎报"未启用"）。
    """
    if mode != "registry":
        return False
    value = _read()
    if value is None:
        return False
    exe = current_exe()
    if exe is None:
        return True
    return _stored_exe(value).lower() == exe.lower()


def apply(mode: str, exe_path: str | None) -> tuple[bool, str]:
    """应用自启设置 → (成功, 说明/错误原因)。任何失败都不抛异常。"""
    if winreg is None:
        return (False, "仅 Windows 支持开机自启")

    if mode == "none":
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            return (True, "开机自启已关闭")
        except OSError as e:
            return (False, f"关闭开机自启失败: {e.strerror or e}")
        return (True, "开机自启已关闭")

    if mode != "registry":
        return (False, f"未知的自启模式: {mode}")

    exe_path = (exe_path or "").strip()
    if not exe_path:
        return (False, "取不到程序路径，无法设置开机自启（源码运行时请用打包后的 exe）")

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command(exe_path))
    except PermissionError:
        return (False, "没有权限写入注册表（被杀软或组策略拦截）")
    except OSError as e:
        return (False, f"写入注册表失败: {e.strerror or e}")
    return (True, "已设为开机自启")
