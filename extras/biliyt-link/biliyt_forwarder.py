#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
biliyt_forwarder.py — biliyt:// 协议轻量转发器（BiliYTPlayer 配套小软件）

这不是播放器本体，只是一个「串联桥梁」，职责仅四条：

  1. 接收浏览器经自定义协议传来的请求：biliyt://open?url=<编码后的地址>
  2. 解析并校验：只放行 http / https，其余一律丢弃
  3. 把 URL 写入系统剪贴板 —— 运行中的 BiliYTPlayer 靠它自带的
     剪贴板监听轮询（0.3s）自动捡起并播放，本体因此零改动
  4. 若播放器没在运行，按配置文件里的路径把它拉起来

依赖：仅 Python 标准库（Windows 下用 ctypes 调 Win32 API），无第三方包。
"""

import os
import re
import sys
import json
import time
import ctypes
import ctypes.wintypes  # 必须显式导入：打包后环境纯净，ctypes.wintypes 不会被自动加载
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

if sys.platform == "win32":
    import winreg  # 仅 Windows：安装时把 biliyt:// 写进注册表

# ---- 常量 ----
PROTOCOL = "biliyt"
CONFIG_NAME = "forwarder_config.json"
LOG_NAME = "biliyt_forwarder.log"
PLAYER_PROC_NAME = "BiliYTPlayer.exe"
CLIPBOARD_RETRY = 12          # 打开剪贴板的重试次数（可能被其它进程短暂占用）
CLIPBOARD_RETRY_INTERVAL = 0.05

_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)

IS_WIN = sys.platform == "win32"


# ===========================================================================
# 1. 命令行参数解析
# ===========================================================================

def parse_launch_url(argv=None):
    """从命令行参数解析出 http(s) URL。

    支持两种形态（都以最终 http/https 校验为准）：
      - biliyt://open?url=<URL 编码后的地址>   ← 油猴脚本 / 系统唤起的标准形态
      - 裸 http(s)://...                      ← 调试用
    其余（file://、cmd:// 等危险协议）一律返回 None，防止协议被滥用。
    """
    argv = list(sys.argv if argv is None else argv)
    for arg in argv[1:]:
        if not isinstance(arg, str):
            continue
        if arg.startswith("biliyt://"):
            qs = parse_qs(urlparse(arg).query)
            raw = (qs.get("url") or [""])[0]
            u = unquote(raw)
            if _HTTP_RE.match(u):
                return u
            continue
        if _HTTP_RE.match(arg):
            return arg
    return None


# ===========================================================================
# 2. 写剪贴板（ctypes + Win32 CF_UNICODETEXT）
# ===========================================================================

def set_clipboard_text(text):
    """把文本写入系统剪贴板。返回是否成功。

    用原生 Win32 API 而不是 pyperclip，保证写入格式与 BiliYTPlayer 的
    _read_clipboard()（CF_UNICODETEXT）完全对齐，且不引第三方依赖。
    """
    if not IS_WIN:
        return False

    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32
    wintypes = ctypes.wintypes  # 已在文件顶部显式 import 该子模块

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    # 句柄一律 HANDLE(c_void_p)，避免 64 位句柄被默认 int 截断
    k32.GlobalAlloc.restype = wintypes.HANDLE
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HANDLE]
    k32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    k32.GlobalFree.argtypes = [wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

    # 剪贴板可能被别的进程短暂锁住，重试几次再放弃
    opened = False
    for _ in range(CLIPBOARD_RETRY):
        if u32.OpenClipboard(0):
            opened = True
            break
        time.sleep(CLIPBOARD_RETRY_INTERVAL)
    if not opened:
        return False

    try:
        u32.EmptyClipboard()
        # CF_UNICODETEXT 要求 UTF-16LE 且以双字节 \0 结尾
        data = text.encode("utf-16-le") + b"\x00\x00"
        hMem = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not hMem:
            return False
        ptr = k32.GlobalLock(hMem)
        if not ptr:
            k32.GlobalFree(hMem)
            return False
        # SetClipboardData 成功后系统接管该内存，不能再 GlobalFree
        ctypes.memmove(ptr, data, len(data))
        k32.GlobalUnlock(hMem)
        if not u32.SetClipboardData(CF_UNICODETEXT, hMem):
            k32.GlobalFree(hMem)
            return False
        return True
    finally:
        u32.CloseClipboard()


# ===========================================================================
# 3. 播放器状态检测与拉起
# ===========================================================================

def load_config():
    """读取同目录配置文件；不存在则返回空 dict（用内置默认）。"""
    try:
        cfg_path = Path(sys.argv[0]).resolve().parent / CONFIG_NAME
        if cfg_path.is_file():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def is_player_running(proc_name=PLAYER_PROC_NAME):
    """播放器是否已在运行。用 tasklist 查询，避免引第三方依赖。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {proc_name}", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc_name.lower() in (out.stdout or "").lower()
    except Exception:
        return False  # 查不到就当作没运行，交给后续逻辑处理


def launch_player(exe_path):
    """启动播放器本体（若配置了路径且文件存在）。"""
    try:
        if exe_path and Path(exe_path).is_file():
            subprocess.Popen(
                [exe_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
    except Exception:
        pass
    return False


# ===========================================================================
# 4. 主流程
# ===========================================================================

def _log(msg):
    """写日志到文件（无窗口模式下看不到控制台，只能落盘）。"""
    try:
        log_path = Path(sys.argv[0]).resolve().parent / LOG_NAME
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ===========================================================================
# 5. 安装 / 卸载（注册能力做进 exe，目标机器无需装 Python）
# ===========================================================================

def _exe_self_path():
    """本程序自身绝对路径（打包后即 exe 自己）。"""
    return str(Path(sys.argv[0]).resolve())


def _notify(text, title="BiliYTPlayer 一键唤起"):
    """结果反馈：写文件 + 弹窗。exe 是 windowed 模式，控制台看不见，只能靠这两招。"""
    try:
        out = Path(sys.argv[0]).resolve().parent / "安装结果.txt"
        out.write_text(text + "\n", encoding="utf-8")
    except Exception:
        pass
    if IS_WIN:
        try:
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
        except Exception:
            pass


def search_player():
    """在常见位置搜索 BiliYTPlayer.exe，返回首个命中或 None。"""
    home = Path.home()
    env = os.environ
    candidates = [
        Path(env.get("LOCALAPPDATA", "")) / "Programs" / "BiliYTPlayer" / "BiliYTPlayer.exe"
        if env.get("LOCALAPPDATA") else None,
        Path(env.get("ProgramFiles", "C:\\Program Files")) / "BiliYTPlayer" / "BiliYTPlayer.exe",
        Path(env.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "BiliYTPlayer" / "BiliYTPlayer.exe",
        home / "Downloads" / "BiliYTPlayer" / "BiliYTPlayer.exe",
        home / "Desktop" / "BiliYTPlayer" / "BiliYTPlayer.exe",
        home / "BiliYTPlayer" / "BiliYTPlayer.exe",
        Path(sys.argv[0]).resolve().parent / "BiliYTPlayer.exe",
    ]
    for drive in ("C:", "D:", "E:"):
        candidates.append(Path(drive) / "BiliYTPlayer" / "BiliYTPlayer.exe")

    for c in candidates:
        try:
            if c and Path(c).is_file():
                return str(c)
        except Exception:
            continue
    return None


def register_protocol(exe_path):
    """把 biliyt:// 注册到 HKCU\\Software\\Classes（当前用户，免管理员）。"""
    if not IS_WIN:
        return False
    base = "Software\\Classes\\" + PROTOCOL
    command = f'"{exe_path}" "%1"'
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, base, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:BiliYTPlayer Protocol")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, base + "\\shell\\open\\command", 0, winreg.KEY_SET_VALUE
        ) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, command)
        return True
    except Exception:
        return False


def unregister_protocol():
    """注销 biliyt://（HKCU 与 HKCR 都尝试清理）。"""
    targets = [
        (winreg.HKEY_CURRENT_USER, "Software\\Classes\\" + PROTOCOL),
        (winreg.HKEY_CLASSES_ROOT, PROTOCOL),
    ]
    removed = False
    for root, base in targets:
        try:
            winreg.DeleteKey(root, base + "\\shell\\open\\command")
            winreg.DeleteKey(root, base + "\\shell\\open")
            winreg.DeleteKey(root, base + "\\shell")
            winreg.DeleteKey(root, base)
            removed = True
        except FileNotFoundError:
            pass
        except Exception:
            pass
    return removed


def save_config(player_path):
    """写配置文件（与 exe 同目录），保留已有字段。

    player_path 为空时不动 player_exe —— 自动探测失败不能把已配好的路径清掉。
    """
    cfg_path = Path(sys.argv[0]).resolve().parent / CONFIG_NAME
    new_vals = {
        "auto_launch_if_not_running": True,
        "launch_wait_sec": 2.0,
    }
    if player_path:
        new_vals["player_exe"] = player_path
    try:
        cfg = {}
        if cfg_path.is_file():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        cfg.update(new_vals)
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def do_install(explicit_path=None):
    """安装：确定播放器路径 → 注册协议 → 写配置。

    路径优先级（重要）：
      拖拽/参数指定  >  已有配置里的有效路径  >  自动探测
    这样自动探测不会覆盖用户已配好的版本（比如把新版换成旧版）。
    """
    self_path = _exe_self_path()
    existing = load_config().get("player_exe", "")
    player = None
    if explicit_path and Path(explicit_path).is_file():
        player = str(Path(explicit_path).resolve())
    elif existing and Path(existing).is_file():
        player = existing  # 尊重已配好的，探测结果不覆盖
    else:
        player = search_player()

    ok_proto = register_protocol(self_path)
    ok_cfg = save_config(player)

    lines = [
        "=" * 44,
        "  BiliYTPlayer 一键唤起 — 安装结果",
        "=" * 44,
        "",
        f"[1] 协议注册: {'成功' if ok_proto else '失败'}",
        f"    biliyt:// -> {self_path}",
        "",
    ]
    if player:
        lines += [
            "[2] 播放器: 已接上",
            f"    {player}",
        ]
    else:
        lines += [
            "[2] 播放器: 没自动找到",
            "    把 BiliYTPlayer.exe 拖到 安装.bat 上，",
            "    或手动改 forwarder_config.json 的 player_exe。",
        ]
    lines += [
        "",
        f"[3] 配置文件: {'已写入' if ok_cfg else '写入失败'}",
        "",
        "最后一步：把 biliyt_launcher.user.js 装进浏览器",
        "的 Tampermonkey（步骤见 使用说明.txt）",
        "=" * 44,
    ]
    _notify("\n".join(lines))
    _log(f"install: proto={ok_proto} cfg={ok_cfg} player={player}")
    return 0 if ok_proto else 1


def do_uninstall():
    removed = unregister_protocol()
    _notify(
        "已注销 biliyt:// 协议。\n\n转发器 exe 和配置文件仍保留在磁盘上，\n"
        "不需要的话直接删掉整个文件夹即可。"
        if removed else
        "未找到已注册的协议（可能已经注销过）。"
    )
    _log(f"uninstall: removed={removed}")
    return 0


# ===========================================================================
# 6. 主流程（子命令分发）
# ===========================================================================

def main():
    args = sys.argv[1:]

    if "--uninstall" in args:
        return do_uninstall()

    if "--install" in args:
        explicit = None
        for a in args:
            if a != "--install" and not a.startswith("--"):
                explicit = a
                break
        return do_install(explicit)

    if "--detect" in args:
        found = search_player()
        _notify(found if found else "未在常见位置找到 BiliYTPlayer.exe", "播放器探测结果")
        return 0

    url = parse_launch_url(sys.argv)
    if not url:
        _log("未解析到合法 http(s) URL，忽略本次请求: " + " ".join(args))
        return 0

    _log(f"转发: {url}")

    cfg = load_config()
    running = is_player_running()
    if not running:
        exe = cfg.get("player_exe", "")
        if cfg.get("auto_launch_if_not_running", True) and launch_player(exe):
            _log(f"播放器未运行，已启动: {exe}")
            # 给本体一点启动时间，别让第一次写的链接错过轮询
            time.sleep(float(cfg.get("launch_wait_sec", 2.0)))
        else:
            _log("播放器未运行且未能自动启动（检查 forwarder_config.json 的 player_exe）")

    if set_clipboard_text(url):
        _log("已写入剪贴板，等待播放器捡起")
        return 0

    _log("写剪贴板失败")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"异常: {e}")
        sys.exit(1)
