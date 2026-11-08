# -*- coding: utf-8 -*-
"""本地开发日志 —— 发布版完全静默。

开关规则（2026-11-09 按需求定）：
  - 源码运行（python bili_yt_player.pyw）  → 写日志
  - PyInstaller 打包后的 exe（sys.frozen） → 不写任何日志文件
  - 本地想临时调试 exe                    → 设环境变量 BILIYTPLAYER_LOG=1

日志落在 %APPDATA%/BiliYTPlayer/app-dev.log，单文件 2MB 轮转一份 .old。
不在仓库目录里，不会被误提交；日志写入失败绝不影响主流程。
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from pathlib import Path

_LOCK = threading.Lock()
_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "BiliYTPlayer"
_PATH = _DIR / "app-dev.log"
_MAX_BYTES = 2 * 1024 * 1024


def enabled() -> bool:
    """源码运行 = 开；打包 exe = 关；环境变量可强制开。"""
    if os.environ.get("BILIYTPLAYER_LOG") == "1":
        return True
    return not getattr(sys, "frozen", False)


def path() -> Path:
    return _PATH


def log(msg: str, tag: str = "") -> None:
    """线程安全地追加一行（含时间戳与线程名，方便追后台线程）。"""
    if not enabled():
        return
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{ts}] [{threading.current_thread().name}] {tag}{msg}\n"
        _DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            if _PATH.exists() and _PATH.stat().st_size > _MAX_BYTES:
                try:
                    _PATH.replace(_DIR / "app-dev.old.log")
                except Exception:
                    pass
            with open(_PATH, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def exception(where: str) -> None:
    """在 except 块内调用：连完整 traceback 一起落盘。"""
    if not enabled():
        return
    import traceback
    log(f"[!] {where}\n{traceback.format_exc()}")
