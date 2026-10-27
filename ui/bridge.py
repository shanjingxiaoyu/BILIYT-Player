# -*- coding: utf-8 -*-
"""ui/bridge.py — Python 业务层 ↔ QML 的唯一通道。

设计要点（对齐 ui-kit/MIGRATION_PROMPT.md 第 126-158 行）：

* 原 tkinter 版的 11 处 `root.after(0, ...)` 全部换成 **Signal.emit()**。
  Qt 的 signal 跨线程 emit 会自动排队到接收者（QML）所在线程，等价且更安全。
* `_log()` / `_add_history()` 这些**已经做了线程安全封装**的函数保持签名不变，
  只把内部实现从 `after` 换成 `emit` —— 调用点一行都不用改。
* `self.history` 这个 Python 侧列表必须继续维护：它是重播的数据源，不是纯 UI 状态。

播放画面 / 进度条 / 弹幕渲染全部在 mpv 里，这里只重写原 1240 行的 GUI 入口。
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

# ---- 后端（零改动）----
# 源码运行：仓库根；PyInstaller：sys._MEIPASS（datas 把 legacy/ 解到这里）。
# 两个路径都要进 sys.path，bridge 用延迟 import 拉 legacy 下的后端模块。
if getattr(sys, "frozen", False):
    _ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    _ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "legacy")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import app_config  # noqa: E402

# ---- 路径（与原 pyw 第 60-71 行一致）----
if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).resolve().parent
else:
    _exe_dir = _ROOT
CONFIG_DIR = Path(os.environ.get("APPDATA", str(_exe_dir))) / "BiliYTPlayer"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
ENV_PATH = CONFIG_DIR / ".env"

BV_RE = re.compile(r"(BV[a-zA-Z0-9]{10})")
YT_RE = re.compile(r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})")
EP_RE = re.compile(r"/ep(\d+)")


def _read_clipboard() -> str:
    """读取剪贴板文本。优先 Win32 API（<1ms），失败则回退 pyperclip。"""
    if sys.platform == "win32":
        try:
            import ctypes
            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.OpenClipboard(0):
                return ""
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ""
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        except Exception:
            pass
    try:
        import pyperclip
        return pyperclip.paste()
    except Exception:
        pass
    return ""


class UiBridge(QObject):
    """QML 侧通过上下文属性 `bridge` 访问本对象。"""

    # ---- 后台线程 → QML 的信号（thread-safe）----
    logLine = Signal(str)                     # "[HH:MM:SS] msg"
    historyAdded = Signal(str, str, str, str)  # time, platform, title, detail
    historyCleared = Signal()
    pausedChanged = Signal()
    readyChanged = Signal()
    toast = Signal(str)
    openSettingsRequested = Signal(str)       # 页签名（空串 = 首页）
    firstRunPrompt = Signal()                 # 无 SESSDATA 时

    # 托盘菜单 / 窗口操作 → QML。用信号而不是让 Python 直接调 QML 函数：
    # QML 里 `function foo()` 声明的东西不是 Python 可调用属性
    # （实测 `win.requestQuit` 报 "object has no attribute"），
    # 走 signal + QML 侧 Connections 才是正路。
    trayShowRequested = Signal()
    traySettingsRequested = Signal()
    trayQuitRequested = Signal()
    minimizeToTrayRequested = Signal()

    # ---- QML → Python（普通槽函数）----
    def __init__(self, parent=None):
        super().__init__(parent)

        # ---- 运行状态 ----
        self.sessdata: str | None = None
        self.player_path: str | None = None
        self.running = False
        self._paused = False
        self._ready = False
        self.history: list[dict] = []
        self.task_queue: queue.Queue = queue.Queue()
        self.recent_ids: deque[str] = deque(maxlen=20)

        # ---- 配置（4 份 conf，格式/位置/字段名一律不变）----
        self.general_cfg = app_config.load_config(CONFIG_DIR)
        self.sponsor_cfg: dict = {"enabled": False}
        self.danmaku_cfg: dict = {"enabled": False}
        self.subtitle_cfg: dict = {"enabled": False}
        self._load_plugin_configs()

        if ENV_PATH.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(ENV_PATH)
                self.sessdata = (os.getenv("SESSDATA", "") or "").strip() or None
            except Exception:
                self.sessdata = None

        self.monitor_thread: threading.Thread | None = None
        self.worker_thread: threading.Thread | None = None

    # ==================================================================
    # 配置加载（与原 __init__ 第 221-243 行一致，任何失败都降级不崩）
    # ==================================================================
    def _load_plugin_configs(self):
        try:
            from sponsorblock import load_config
            self.sponsor_cfg = load_config(CONFIG_DIR, log=lambda m: self._log(m))
        except Exception as e:
            self._log(f"[!] SponsorBlock 配置加载失败，功能停用: {e}")
            self.sponsor_cfg = {"enabled": False}
        try:
            from danmaku import load_config as load_danmaku
            self.danmaku_cfg = load_danmaku(CONFIG_DIR, log=lambda m: self._log(m))
        except Exception as e:
            self._log(f"[!] 弹幕配置加载失败，功能停用: {e}")
            self.danmaku_cfg = {"enabled": False}
        try:
            from subtitle import load_config as load_subtitle
            self.subtitle_cfg = load_subtitle(CONFIG_DIR, log=lambda m: self._log(m))
        except Exception as e:
            self._log(f"[!] 字幕配置加载失败，功能停用: {e}")
            self.subtitle_cfg = {"enabled": False}

    @Slot()
    def reloadConfigs(self):
        """设置页保存后由 QML 调用：重读 4 份 conf，刷新所有开关状态。"""
        self.general_cfg = app_config.load_config(CONFIG_DIR)
        self._load_plugin_configs()
        self._notify_switches()
        self._log("[*] 设置已重新载入（播放下一个视频生效）")

    # ==================================================================
    # 日志 / 历史（线程安全：任意后台线程都可调用）
    # ==================================================================
    def _log(self, msg):
        """签名与原 pyw 第 771 行的 _log 一致，内部实现从 after 换成 emit。"""
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            self.logLine.emit(f"[{ts}] {msg}")
        except Exception:
            pass

    def _add_history(self, platform, vid, title, quality, audio, raw_id=None):
        """签名与原 pyw 第 785 行一致。后台线程调用安全。

        vid     —— 展示用的标签（如 "ep123" / BV 号）
        raw_id  —— 供重播使用的裸 ID（如 "123"）
        """
        ts = datetime.now().strftime("%H:%M")
        entry = {
            "time": ts, "platform": platform, "vid": vid, "title": title,
            "detail": f"{quality}  {audio}",
            "raw_id": str(raw_id if raw_id is not None else vid),
        }
        self.history.append(entry)
        try:
            self.historyAdded.emit(entry["time"], entry["platform"],
                                   entry["title"], entry["detail"])
        except Exception:
            pass

    @Slot()
    def clearHistory(self):
        self.history.clear()
        self.historyCleared.emit()
        self.toast.emit("已清空播放历史")

    @Slot(int)
    def replay(self, idx: int):
        """双击历史条目 → 重播。等价于原 _on_history_double_click 的业务部分。"""
        if not (0 <= idx < len(self.history)):
            return
        entry = self.history[idx]
        if not self.ready:
            self._log("[!] 尚未就绪，无法重播")
            return
        self._log(f">> 重播历史: {entry['platform']} {entry['vid']}")
        self.task_queue.put((self._type_for(entry["platform"]), entry["raw_id"]))

    @staticmethod
    def _type_for(platform):
        """把历史条目的「来源」映射回任务类型（原 pyw 第 837 行）。"""
        return {
            "B站": "bili", "番剧": "ep", "番剧合集": "ss",
            "媒体详情": "md", "YouTube": "yt",
        }.get(platform, "bili")

    # ==================================================================
    # QML 可读属性
    # ==================================================================
    pausedChanged = Signal()
    readyChanged = Signal()

    def _get_paused(self):
        return self._paused

    def _set_paused(self, v):
        self._paused = bool(v)
        self.pausedChanged.emit()

    paused = Property(bool, _get_paused, _set_paused, notify=pausedChanged)

    def _get_ready(self):
        return self._ready

    def _set_ready(self, v):
        self._ready = bool(v)
        self.readyChanged.emit()

    ready = Property(bool, _get_ready, _set_ready, notify=readyChanged)

    # 三个开关：读写 conf 并持久化
    # （等价原 pyw 的 _toggle_sponsorblock / _toggle_danmaku / _toggle_subtitle）
    skipChanged = Signal()
    danmakuChanged = Signal()
    subtitleChanged = Signal()

    def _get_skip(self):
        return bool(self.sponsor_cfg.get("enabled", True))

    def _set_skip(self, v):
        self.sponsor_cfg["enabled"] = bool(v)
        try:
            from sponsorblock import save_config
            save_config(CONFIG_DIR, self.sponsor_cfg)
        except Exception as e:
            self._log(f"[!] 配置保存失败: {e}")
        self.skipChanged.emit()
        self._log("[*] 广告跳过已开启。" if v else "[*] 广告跳过已关闭。")

    skipEnabled = Property(bool, _get_skip, _set_skip, notify=skipChanged)

    def _get_dm(self):
        return bool(self.danmaku_cfg.get("enabled", True))

    def _set_dm(self, v):
        self.danmaku_cfg["enabled"] = bool(v)
        try:
            from danmaku import save_config
            save_config(CONFIG_DIR, self.danmaku_cfg)
        except Exception as e:
            self._log(f"[!] 弹幕配置保存失败: {e}")
        self.danmakuChanged.emit()
        self._log("[*] 弹幕已开启（下个视频生效）。" if v
                  else "[*] 弹幕已关闭（下个视频生效）。")

    danmakuEnabled = Property(bool, _get_dm, _set_dm, notify=danmakuChanged)

    def _get_cc(self):
        return bool(self.subtitle_cfg.get("enabled", True))

    def _set_cc(self, v):
        self.subtitle_cfg["enabled"] = bool(v)
        try:
            from subtitle import save_config
            save_config(CONFIG_DIR, self.subtitle_cfg)
        except Exception as e:
            self._log(f"[!] 字幕配置保存失败: {e}")
        self.subtitleChanged.emit()
        self._log("[*] 原生字幕已开启（下个视频生效）。" if v
                  else "[*] 原生字幕已关闭（下个视频生效）。")

    subtitleEnabled = Property(bool, _get_cc, _set_cc, notify=subtitleChanged)

    def _notify_switches(self):
        self.skipChanged.emit()
        self.danmakuChanged.emit()
        self.subtitleChanged.emit()

    @Property(str, constant=True)
    def qualityLabel(self):
        """历史/状态区展示用的当前画质偏好。"""
        try:
            from app.quality import QUALITY_OPTIONS
            pref = str(self.general_cfg.get("quality", "auto"))
            for k, label in QUALITY_OPTIONS:
                if k == pref:
                    return label
        except Exception:
            pass
        return "自动（最高可用）"

    # ==================================================================
    # 设置 / 关闭行为
    # ==================================================================
    @Slot(str)
    def openSettings(self, page: str = ""):
        self.openSettingsRequested.emit(page)

    @Slot(result=str)
    def configDir(self):
        return str(CONFIG_DIR)

    @Slot(result=bool)
    def autostartEnabled(self):
        try:
            from app import autostart
            return autostart.is_enabled(str(self.general_cfg.get("autostart_mode", "none")))
        except Exception:
            return False

    @Slot(result=str)
    def closeAction(self):
        return str(self.general_cfg.get("close_action", "ask"))

    @Slot(result=bool)
    def askOnClose(self):
        return bool(self.general_cfg.get("ask_on_close", True))

    @Slot(str, bool)
    def saveCloseChoice(self, action: str, remember: bool):
        """CloseDialog 的 accepted(action, remember) 落到 general.conf。

        remember=True  → 记住这次选择，以后不再问（ask_on_close=False）
        remember=False → 下次仍然弹询问框
        """
        cfg = dict(self.general_cfg)
        cfg["close_action"] = "close" if action == "close" else "tray"
        cfg["ask_on_close"] = not bool(remember)
        if app_config.save_config(CONFIG_DIR, cfg):
            self.general_cfg = cfg
            self._log(f"[*] 关闭行为已更新：{'退出程序' if action == 'close' else '最小化到托盘'}"
                      f"{'（以后不再询问）' if remember else ''}")
        else:
            self._log("[!] 关闭行为保存失败，请检查配置目录权限")

    # ==================================================================
    # 初始化 / 监听（线程模型与原 pyw 完全一致）
    # ==================================================================
    @Slot()
    def bootstrap(self):
        """入口在 QML 加载完成后调用：有 SESSDATA 就走鉴权，没有就提示配置。"""
        self._log("正在初始化…")
        if not self.sessdata:
            self._log("[!] 未配置 SESSDATA —— B 站播放需要登录态")
            self.firstRunPrompt.emit()
            self.openSettingsRequested.emit("account")
            return
        threading.Thread(target=self._init_backend, daemon=True).start()

    def _init_backend(self):
        from bili_clipboard_dolby import find_player
        self._log("[*] 检测播放器…")
        player_path = find_player()
        if player_path:
            self._continue_init(player_path)
        else:
            self._log("[!] 未找到播放器 —— 请把 mpv-portable/ 放到程序同级目录")
            self.toast.emit("未找到 mpv，播放功能不可用")

    def _continue_init(self, player_path):
        """播放器已确定，继续鉴权并启动监听（原 pyw 第 921 行）。"""
        from bili_clipboard_dolby import init_bili_session

        self.player_path = player_path
        self._log(f"[+] 播放器: {player_path}")
        self._log("[*] B 站鉴权… 连接 B站 API")

        try:
            self.session, self.img_key, self.sub_key = init_bili_session(self.sessdata)
            self._log("[+] B 站鉴权就绪。")
        except Exception as e:
            self._log(f"[!] B 站鉴权失败: {e}")
            self._log("[*] SESSDATA 可能已过期，请在「设置 → B站账号」重新填写")
            self.openSettingsRequested.emit("account")
            return

        self.ready = True
        self._log("[*] 监听中 — 复制 B 站 / YouTube 链接即可播放。")
        self.startMonitor()

    @Slot()
    def startMonitor(self):
        if self.running:
            return
        self.running = True
        self.paused = False
        # 监听线程：轻量，只读剪贴板 + 正则匹配 → 入队
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        # 工作线程：从队列取任务，做 API 调用 + 启动播放器
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    @Slot()
    def togglePause(self):
        self.paused = not self.paused
        self._log("[*] 已暂停监听。" if self.paused else "[*] 已恢复监听。")
        self.toast.emit("已暂停监听" if self.paused else "已恢复监听")

    _LINK_PATTERNS = (
        (BV_RE, "bili"),
        (YT_RE, "yt"),
        (EP_RE, "ep"),
        (re.compile(r"/ss(\d+)"), "ss"),
        (re.compile(r"/md(\d+)"), "md"),
    )

    @classmethod
    def _match_link(cls, text: str):
        for pattern, type_name in cls._LINK_PATTERNS:
            m = pattern.search(text)
            if m:
                return (type_name, m.group(1))
        return None

    def _monitor_loop(self):
        """监听线程：只做剪贴板读取 + 正则匹配，不做任何网络请求（.cursorrules:60）。"""
        while self.running:
            if self.paused:
                time.sleep(0.3)
                continue
            text = _read_clipboard()
            result = self._match_link(text)
            if result:
                _type, raw_id = result
                dedup_key = f"{_type}:{raw_id}"
                if dedup_key not in self.recent_ids:
                    self.recent_ids.append(dedup_key)
                    self.task_queue.put((_type, raw_id))
            time.sleep(0.3)

    def _worker_loop(self):
        while self.running:
            try:
                task = self.task_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if self.paused:
                self.task_queue.put(task)
                time.sleep(0.5)
                continue
            self._process_task(task)

    def _process_task(self, task):
        _type, raw_id = task
        try:
            if _type == "bili":
                self._log(f">> B 站: {raw_id}")
                self._play_bili(raw_id)
            elif _type == "yt":
                self._log(f">> YouTube: {raw_id}")
                self._play_yt(raw_id)
            elif _type == "ep":
                self._log(f">> 番剧/电影 EP: {raw_id}")
                self._play_episode(int(raw_id))
            elif _type == "ss":
                self._log(f">> 番剧合集 SS: {raw_id}")
                self._play_ss(int(raw_id))
            elif _type == "md":
                self._log(f">> 媒体详情 MD: {raw_id}")
                self._play_md(int(raw_id))
        except Exception as e:
            self._log(f"  [!] {e}")

    # ==================================================================
    # 播放
    # ==================================================================
    def _quality_pref(self):
        """当前画质偏好 → (qn 参数, max_qn 上限)。异常一律降级为 auto。"""
        try:
            from app.quality import qn_from_pref
            pref = str(self.general_cfg.get("quality", "auto"))
            qn = qn_from_pref(pref)
            return qn, (0 if pref == "auto" else qn)
        except Exception:
            return 126, 0

    def _play_bili_stream(self, bvid, cid, title, platform, vid_label, raw_id=None):
        """B 站系（正片 / 番剧 / 合集 / 媒体页）统一播放流程（原 pyw 第 1043 行）。"""
        from bili_clipboard_dolby import get_playurl, pick_dolby_streams, launch_player

        qn, max_qn = self._quality_pref()
        data = get_playurl(self.session, bvid, cid, self.img_key, self.sub_key, qn=qn)
        dash = data.get("dash")
        if not dash:
            self._log("  [!] 无 DASH 数据")
            return
        vurl, aurl, vd, ad = pick_dolby_streams(dash, max_qn=max_qn)
        if not vurl:
            self._log("  [!] 未能提取可播放的视频流")
            return
        self._log(f"  [+] 画质: {vd}")

        # ---- 弹幕（ASS 外挂）----
        danmaku_ass, dm_count = self._prepare_danmaku(cid, dash, max_qn)
        # ---- 原生字幕（与弹幕并存，走次轨）----
        subtitle_ass, cc_lang = self._prepare_subtitle(bvid, cid, dash, max_qn)

        segments = self._fetch_sponsor_segments(bvid, cid)
        summary = self._summarize_segments(segments)
        if self.sponsor_cfg.get("enabled", True):
            self._log(f"  [*] SponsorBlock: {'命中 ' + summary if summary else '无片段'}")

        parts = []
        if summary:
            parts.append(summary)
        if dm_count:
            parts.append(f"弹幕 {dm_count} 条")
        if cc_lang:
            parts.append(f"字幕 {cc_lang}")
        suffix = ("  |  " + "  ".join(parts)) if parts else ""

        self._add_history(platform, vid_label, title, vd,
                          (ad or "普通音频") + suffix, raw_id=raw_id)

        cfg = dict(self.sponsor_cfg)
        cfg["bvid"] = bvid
        cfg["cid"] = cid
        launch_player(
            self.player_path, vurl, title,
            audio_url=aurl, sessdata=self.sessdata, log=self._log,
            segments=segments, sponsor_cfg=cfg,
            danmaku_ass=danmaku_ass, subtitle_file=subtitle_ass,
        )

    def _prepare_danmaku(self, cid, dash, max_qn=0):
        """下载并转换弹幕 → (ass路径或None, 条数)。任何失败都降级（.cursorrules:57）。"""
        if not self.danmaku_cfg.get("enabled", True):
            return None, 0
        try:
            from danmaku import download_and_convert, prune_cache
            width, height = self._stream_size(dash, max_qn)
            path, n = download_and_convert(
                cid, width, height,
                config_dir=CONFIG_DIR, cfg=self.danmaku_cfg, log=self._log,
            )
            if path:
                self._log(f"  [*] 弹幕: {n} 条")
                try:
                    prune_cache(CONFIG_DIR, limit=int(self.danmaku_cfg.get("cache_limit", 200)))
                except Exception:
                    pass
            return path, n
        except Exception as e:
            self._log(f"  [!] 弹幕处理失败（已忽略）: {e}")
            return None, 0

    def _prepare_subtitle(self, bvid, cid, dash, max_qn=0):
        """获取原生字幕 → (ass路径或None, 语言)。失败降级。"""
        if not self.subtitle_cfg.get("enabled", True):
            return None, ""
        try:
            from subtitle import ensure_subtitle_ass
            width, height = self._stream_size(dash, max_qn)
            path, lang = ensure_subtitle_ass(
                self.session, bvid, cid, width, height,
                config_dir=CONFIG_DIR, cfg=self.subtitle_cfg, log=self._log,
            )
            if path:
                self._log(f"  [*] 原生字幕: {lang}")
            return path, lang
        except Exception as e:
            self._log(f"  [!] 字幕处理失败（已忽略）: {e}")
            return None, ""

    def _stream_size(self, dash, max_qn=0):
        """取所选视频流的宽高；ASS 的 PlayRes 需要它做正确缩放（原 pyw 第 1136 行）。

        必须带上 max_qn：用户把画质锁到 720P 时，实际播放的是 720P 流，
        若仍按 4K 的宽高算 PlayRes，弹幕字号会按 4K 缩放到只剩 1.2% 屏高（小到看不清）。
        走 app.quality.pick_stream，保证这里挑中的流和实际播放的流是同一档。
        """
        try:
            vids = (dash or {}).get("video") or []
            if vids:
                try:
                    from app.quality import pick_stream
                    best, _ = pick_stream(vids, max_qn)
                except Exception:
                    best = max(vids, key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)))
                if best:
                    w, h = int(best.get("width") or 0), int(best.get("height") or 0)
                    if w > 0 and h > 0:
                        return w, h
        except Exception:
            pass
        return 1920, 1080

    def _fetch_sponsor_segments(self, bvid, cid):
        """查询 SponsorBlock 片段。未启用或失败返回空列表（绝不影响播放）。"""
        if not self.sponsor_cfg.get("enabled", True):
            return []
        try:
            from sponsorblock import (
                fetch_segments, filter_segments, active_categories, CATEGORY_TIERS,
            )
            raw = fetch_segments(
                bvid, cid,
                server=self.sponsor_cfg.get("server"),
                timeout=self.sponsor_cfg.get("timeout"),
                log=self._log,
            )
            tiers = self.sponsor_cfg.get("category_tiers") or dict(CATEGORY_TIERS)
            return filter_segments(
                raw,
                active_categories(tiers),
                min_duration=float(self.sponsor_cfg.get("min_duration", 1.0)),
                tiers=tiers,
            )
        except Exception as e:
            self._log(f"    [!] SponsorBlock 处理失败（已忽略）: {e}")
            return []

    @staticmethod
    def _summarize_segments(segments) -> str:
        if not segments:
            return ""
        total = sum(max(0.0, s["end"] - s["start"]) for s in segments)
        return f"跳过 {len(segments)} 段 {int(total // 60)}m{int(total % 60)}s"

    def _play_bili(self, bvid):
        from bili_clipboard_dolby import get_cid
        cid, title = get_cid(self.session, bvid)
        self._play_bili_stream(bvid, cid, title, "B站", bvid)

    def _play_episode(self, ep_id: int):
        from bili_clipboard_dolby import resolve_episode
        bvid, cid, full_title = resolve_episode(self.session, ep_id)
        self._log(f"  [+] {full_title} (BV={bvid})")
        self._play_bili_stream(bvid, cid, full_title, "番剧", f"ep{ep_id}", raw_id=ep_id)

    def _play_ss(self, ss_id: int):
        from bili_clipboard_dolby import resolve_ss
        bvid, cid, full_title = resolve_ss(self.session, ss_id)
        self._log(f"  [+] {full_title} (BV={bvid})")
        self._play_bili_stream(bvid, cid, full_title, "番剧合集", f"ss{ss_id}", raw_id=ss_id)

    def _play_md(self, md_id: int):
        from bili_clipboard_dolby import resolve_md
        bvid, cid, full_title = resolve_md(self.session, md_id)
        self._log(f"  [+] {full_title} (BV={bvid})")
        self._play_bili_stream(bvid, cid, full_title, "媒体详情", f"md{md_id}", raw_id=md_id)

    def _play_yt(self, ytid):
        from bili_clipboard_dolby import launch_player
        url = f"https://www.youtube.com/watch?v={ytid}"
        self._log(f"    唤起播放器: {url[:60]}…")
        try:
            launch_player(self.player_path, url, url, log=self._log)
            self._add_history("YouTube", ytid, url, "yt-dlp 最高画质", "")
        except Exception as e:
            self._log(f"    [!] YT launch 失败: {e}")

    # ==================================================================
    # 退出
    # ==================================================================
    def shutdown(self):
        self.running = False
        time.sleep(0.05)
