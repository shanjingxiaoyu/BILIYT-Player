#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bili_yt_player.pyw — 剪贴板直连播放器 GUI

双击启动：首次输入 SESSDATA，之后最小化到后台，复制 B 站/YouTube 链接即播。
"""

import sys
assert sys.version_info >= (3, 10), "需要 Python 3.10+"

import os
import sys
import re
import time
import queue
import threading
import tkinter as tk
from collections import deque
from tkinter import ttk, filedialog
from pathlib import Path
from datetime import datetime
import subprocess as sp
from dotenv import load_dotenv

# ---- subprocess helpers: hide console window on Windows (no flashing cmd) ----
def _popen_silent(*args, **kwargs):
    """Popen with hidden console on Windows."""
    if sys.platform == "win32":
        si = sp.STARTUPINFO()
        si.dwFlags |= sp.STARTF_USESHOWWINDOW
        si.wShowWindow = sp.SW_HIDE
        kwargs.setdefault("startupinfo", si)
        kwargs.setdefault("creationflags", sp.CREATE_NO_WINDOW)
    return sp.Popen(*args, **kwargs)

def _run_silent(*args, **kwargs):
    """subprocess.run with hidden console on Windows."""
    if sys.platform == "win32":
        si = sp.STARTUPINFO()
        si.dwFlags |= sp.STARTF_USESHOWWINDOW
        si.wShowWindow = sp.SW_HIDE
        kwargs.setdefault("startupinfo", si)
        kwargs.setdefault("creationflags", sp.CREATE_NO_WINDOW)
    return sp.run(*args, **kwargs)

# ---- PyInstaller --windowed 模式下 sys.stdout/stderr 为 None，任何 print() 都会崩溃 ----
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# 强制 UTF-8 编码,避免标题中的特殊字符(如 ®)触发 GBK 编码崩溃
for _fh in (sys.stdout, sys.stderr):
    try:
        _fh.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# PyInstaller 打包后 __file__ 指向临时目录，改用 sys.executable
# 配置目录：优先使用 %APPDATA%，确保 exe 放在 Program Files 等受限目录时也有写权限
if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).resolve().parent
else:
    _exe_dir = Path(__file__).resolve().parent
_CONFIG_DIR = Path(os.environ.get("APPDATA", str(_exe_dir))) / "BiliYTPlayer"
_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
ENV_PATH = _CONFIG_DIR / ".env"
BV_RE = re.compile(r"(BV[a-zA-Z0-9]{10})")
YT_RE = re.compile(r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})")
EP_RE = re.compile(r"/ep(\d+)")
P_PARAM_RE = re.compile(r"[?&]p=(\d+)")  # 分 P 参数


# =============================================================================
# 扫码登录（QR）— 替代手工凭证与浏览器 HTML 配置页
# 具体实现见 App._qr_login_window / App._draw_qr_canvas
# =============================================================================


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("剪贴板直连播放器 — B站 / YouTube")
        self.root.geometry("480x420")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        self.sessdata = None
        self.running = False
        self.paused = False
        self.history: list[str] = []
        self.task_queue: queue.Queue = queue.Queue()
        self.recent_ids: deque[str] = deque(maxlen=20)

        if ENV_PATH.exists():
            load_dotenv(ENV_PATH)
            self.sessdata = os.getenv("SESSDATA", "").strip() or None

        self._build_ui()

        if not self.sessdata:
            # 无 SESSDATA → 弹窗扫码登录（阻塞主线程直到完成/取消）
            self.sessdata = self._qr_login_window()
            if not self.sessdata:
                self.root.destroy()
                sys.exit(0)

        # 后台线程做 SESSDATA 校验+初始化,避免网络阻塞 GUI
        self._show_status_page()

    # ---------- UI ----------
    def _build_ui(self):
        self.main_frame = ttk.Frame(self.root, padding=15)
        self.main_frame.pack(fill="both", expand=True)

        ttk.Label(
            self.main_frame, text="剪贴板直连播放器 — B站 / YouTube",
            font=("Microsoft YaHei", 12, "bold"),
        ).pack(pady=(0, 10))

        # ---- 状态日志 ----
        log_frame = ttk.LabelFrame(self.main_frame, text="状态", padding=6)
        log_frame.pack(fill="both", expand=True)

        self.status_text = tk.Text(
            log_frame, height=10, width=56, state="disabled",
            font=("Consolas", 9), bg="#fafafa", relief="flat", border=0,
        )
        self.status_text.pack(fill="both", expand=True)

        # ---- 播放历史 ----
        hist_frame = ttk.LabelFrame(self.main_frame, text="播放历史", padding=6)
        hist_frame.pack(fill="both", expand=True, pady=(8, 0))

        self.hist_text = tk.Text(
            hist_frame, height=4, width=56, state="disabled",
            font=("Consolas", 9), bg="#fafafa", relief="flat", border=0,
        )
        self.hist_text.pack(fill="both", expand=True)

        # ---- 按钮栏 ----
        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.pack(fill="x", pady=(10, 0))

        self.pause_btn = ttk.Button(btn_frame, text="暂停监听", command=self.toggle_pause)
        self.pause_btn.pack(side="left")

        ttk.Button(btn_frame, text="清空历史", command=self._clear_history).pack(side="left", padx=6)

        ttk.Button(btn_frame, text="退出", command=self._quit).pack(side="right")

    def _log(self, msg):
        """线程安全：所有调用方都可能来自后台线程，统一通过 root.after 调度到主线程。"""
        try:
            self.root.after(0, self._log_ui, msg)
        except Exception:
            pass

    def _log_ui(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.status_text.configure(state="normal")
        self.status_text.insert("end", f"[{ts}] {msg}\n")
        self.status_text.see("end")
        self.status_text.configure(state="disabled")

    def _add_history(self, platform, vid, title, quality, audio):
        """线程安全：后台线程只做数据处理，UI 操作通过 root.after 调度到主线程。"""
        ts = datetime.now().strftime("%H:%M")
        line = f"[{ts}] {platform} {vid}  {title}  |  {quality}  {audio}"
        self.history.append(line)
        self.root.after(0, self._add_history_ui, line)

    def _add_history_ui(self, line):
        """主线程执行：安全操作 tkinter 控件。"""
        self.hist_text.configure(state="normal")
        self.hist_text.insert("end", line + "\n")
        self.hist_text.see("end")
        self.hist_text.configure(state="disabled")

    def _clear_history(self):
        self.history.clear()
        self.hist_text.configure(state="normal")
        self.hist_text.delete("1.0", "end")
        self.hist_text.configure(state="disabled")

    # ---------- SESSDATA 扫码登录 ----------
    def _qr_login_window(self) -> str | None:
        """弹窗显示 B 站登录二维码，后台线程轮询扫码。返回 SESSDATA 或 None。"""
        from bili_clipboard_dolby import qr_login_sessdata

        result = {"sessdata": None}
        cancel_evt = threading.Event()

        win = tk.Toplevel(self.root)
        win.title("B 站扫码登录")
        win.geometry("340x440")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", lambda: cancel_evt.set())

        canvas = tk.Canvas(win, width=290, height=290, bg="white", highlightthickness=0)
        canvas.pack(pady=(20, 8))

        status_lbl = ttk.Label(win, text="正在获取二维码…", font=("Microsoft YaHei", 10, "bold"))
        status_lbl.pack(pady=(0, 4))
        ttk.Label(
            win, text="使用 B 站 App「扫一扫」，在手机上确认登录",
            foreground="#888", font=("Microsoft YaHei", 9),
        ).pack(pady=(0, 14))
        ttk.Button(win, text="取消", command=lambda: cancel_evt.set()).pack(pady=(0, 16))

        def draw(matrix):
            self.root.after(0, lambda: self._draw_qr_canvas(canvas, matrix))

        def on_status(msg):
            self.root.after(0, lambda: status_lbl.configure(text=msg))

        def run():
            sd = qr_login_sessdata(
                emit=self._log, draw_qr=draw, on_status=on_status, cancel=cancel_evt,
            )
            result["sessdata"] = sd
            self.root.after(0, win.destroy)

        threading.Thread(target=run, daemon=True).start()
        self.root.wait_window(win)  # 主线程局部事件循环，阻塞至扫码完成/取消
        return result["sessdata"]

    @staticmethod
    def _draw_qr_canvas(canvas, matrix):
        """把 qrcode 矩阵画到 tkinter Canvas（不依赖 PIL）。"""
        canvas.delete("all")
        n = len(matrix)
        if n <= 0:
            return
        cell = 280 // n
        off = (290 - cell * n) // 2
        for y, row in enumerate(matrix):
            for x, v in enumerate(row):
                if v:
                    canvas.create_rectangle(
                        off + x * cell, off + y * cell,
                        off + (x + 1) * cell, off + (y + 1) * cell,
                        fill="black", outline="",
                    )

    # ---------- 后端初始化 ----------
    def _show_status_page(self):
        self._log("正在初始化…")
        threading.Thread(target=self._init_backend, daemon=True).start()

    def _init_backend(self):
        from bili_clipboard_dolby import find_player
        self._log("[*] 检测播放器…")
        player_path = find_player()
        if player_path:
            self._continue_init(player_path)
        else:
            self._log("[!] 未找到播放器,请手动选择…")
            # 文件对话框必须在主线程,用 after 调度
            self.root.after(0, lambda: self._ask_player_and_init())

    def _ask_player_and_init(self):
        """在主线程弹出文件对话框,然后继续初始化。"""
        path = filedialog.askopenfilename(
            title="未找到 mpv.exe，请手动选择（推荐用便携版 mpv）",
            filetypes=[("mpv", "mpv.exe"), ("所有文件", "*.*")],
        )
        if path:
            self._continue_init(path)
        else:
            self._log("[!] 未选择播放器,功能不可用。")
            self.root.after(0, lambda: self.pause_btn.configure(state="disabled"))

    def _continue_init(self, player_path):
        """播放器已确定，继续初始化 B 站鉴权和监听。"""
        from bili_clipboard_dolby import init_bili_session

        self.player_path = player_path
        self._log(f"[+] 播放器: {player_path}")

        # ---- B 站鉴权（单次 nav API 完成验证 + WBI 密钥获取） ----
        self._log("[*] B 站鉴权… 连接B站API")

        try:
            self.session, self.img_key, self.sub_key = init_bili_session(self.sessdata)
            self._log("[+] B 站鉴权就绪。")
        except Exception as e:
            self._log(f"[!] B 站鉴权失败: {e}")
            self._log("[*] SESSDATA 已过期，正在弹出扫码登录…")
            ENV_PATH.unlink(missing_ok=True)
            # 弹窗必须在主线程，后台线程等待结果
            done = threading.Event()
            holder = {}

            def do_login():
                holder["sd"] = self._qr_login_window()
                done.set()

            self.root.after(0, do_login)
            done.wait(timeout=240)  # 最多等 4 分钟（二维码有效期 + 轮询超时）
            self.sessdata = holder.get("sd")
            if not self.sessdata:
                self._log("[!] 扫码登录未完成。")
                self.root.after(0, self._quit)
                return
            try:
                self.session, self.img_key, self.sub_key = init_bili_session(self.sessdata)
                self._log("[+] B 站鉴权就绪。")
            except Exception as e2:
                self._log(f"[!] B 站鉴权仍然失败: {e2}")
                self.root.after(0, lambda: self.pause_btn.configure(state="disabled"))
                return

        self._log("[*] 监听中 — 复制 B 站 / YouTube 链接即可播放。")
        self.root.after(0, lambda: self.pause_btn.configure(text="暂停监听", state="normal"))
        self.root.after(0, self.start_monitor)

    # ---------- 监听控制 ----------
    _LINK_PATTERNS = [
        (BV_RE, "bili"),
        (YT_RE, "yt"),
        (EP_RE, "ep"),
        (re.compile(r"/ss(\d+)"), "ss"),
        (re.compile(r"/md(\d+)"), "md"),
    ]

    def start_monitor(self):
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

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.pause_btn.configure(text="恢复监听")
            self._log("[*] 已暂停监听。")
        else:
            self.pause_btn.configure(text="暂停监听")
            self._log("[*] 已恢复监听。")

    def _match_link(self, text: str):
        """从剪贴板文本中匹配链接，返回 (type, raw_id, extra) 或 None。
        extra: bili 类型为分 P 参数（默认 1），其余类型为 None。"""
        for pattern, type_name in self._LINK_PATTERNS:
            m = pattern.search(text)
            if m:
                if type_name == "bili":
                    pm = P_PARAM_RE.search(text)
                    p = int(pm.group(1)) if pm else 1
                    return (type_name, m.group(1), p)
                return (type_name, m.group(1), None)
        return None

    def _monitor_loop(self):
        """监听线程：只做剪贴板读取 + 正则匹配，检测到新链接就入队，不做任何网络请求。"""
        while self.running:
            if self.paused:
                time.sleep(0.3)
                continue
            text = _read_clipboard()
            result = self._match_link(text)
            if result:
                _type, raw_id, extra = result
                # 去重键：B 站升级为 {bvid}_p{p}，避免切换分 P 被误拦截
                if _type == "bili":
                    dedup_key = f"bili:{raw_id}_p{extra}"
                else:
                    dedup_key = f"{_type}:{raw_id}"
                if dedup_key not in self.recent_ids:
                    self.recent_ids.append(dedup_key)
                    self.task_queue.put((_type, raw_id, extra))
            time.sleep(0.3)

    def _worker_loop(self):
        """工作线程：从队列取任务，执行 API 调用和播放器启动。慢不慢都不影响监听。"""
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
        _type, raw_id, extra = task
        try:
            if _type == "bili":
                self._log(f">> B 站: {raw_id}" + (f" (P{extra})" if extra else ""))
                self._play_bili_id("bili", raw_id, extra or 1)
            elif _type == "yt":
                self._log(f">> YouTube: {raw_id}")
                self._play_yt(raw_id)
            elif _type == "ep":
                self._log(f">> 番剧/电影 EP: {raw_id}")
                self._play_bili_id("ep", int(raw_id))
            elif _type == "ss":
                self._log(f">> 番剧合集 SS: {raw_id}")
                self._play_bili_id("ss", int(raw_id))
            elif _type == "md":
                self._log(f">> 媒体详情 MD: {raw_id}")
                self._play_bili_id("md", int(raw_id))
        except Exception as e:
            self._log(f"  [!] {e}")

    # ---------- 播放 ----------
    def _episode_ctx(self) -> dict:
        """切集线程所需的播放上下文（session + WBI 密钥 + 登录态）。"""
        return {
            "session": self.session, "img_key": self.img_key,
            "sub_key": self.sub_key, "sessdata": self.sessdata,
        }

    def _setup_episodes(self, source_type, source_id, cid):
        """获取剧集列表并写入切集状态（Ctrl+←→ 切集 + 播完自动连播）。"""
        from bili_clipboard_dolby import fetch_episode_list, set_episode_state
        try:
            eps = fetch_episode_list(self.session, source_type, source_id)
            index = next((i for i, e in enumerate(eps) if e.get("cid") == cid), 0)
            set_episode_state(eps, index, self._episode_ctx())
            self._log(f"  [*] 剧集列表 {len(eps)} 集，当前第 {index + 1} 集")
        except Exception as e:
            self._log(f"  [!] 剧集列表获取失败，自动连播不可用: {e}")

    def _play_dash(self, platform, vid_label, bvid, cid, title):
        """公共播放链路：取 DASH → 选流 → 记历史 → 唤起 mpv。"""
        from bili_clipboard_dolby import get_playurl, pick_dolby_streams, launch_player
        data = get_playurl(self.session, bvid, cid, self.img_key, self.sub_key)
        dash = data.get("dash")
        if not dash:
            self._log("  [!] 无 DASH 数据")
            return
        vurl, aurl, vd, ad = pick_dolby_streams(dash)
        self._add_history(platform, vid_label, title, vd, ad or "普通音频")
        launch_player(self.player_path, vurl, title, audio_url=aurl,
                      sessdata=self.sessdata, log=self._log)

    def _play_bili_id(self, kind, raw_id, p=1):
        """B 站各 ID 类型统一入口：解析 → （剧集列表）→ DASH 播放。

        kind: bili(BV) / ep(单集) / ss(合集) / md(媒体详情)。四套逻辑合并于此，
        以后改播放链路只动这一个方法。md 无 season id，不支持切集连播。
        """
        from bili_clipboard_dolby import (
            get_cid_and_title, resolve_episode, resolve_ss, resolve_md,
        )
        if kind == "bili":
            cid, title = get_cid_and_title(self.session, raw_id, p=p)
            bvid, label, platform, eptype = raw_id, f"{raw_id}_p{p}", "B站", ("bili", raw_id)
        elif kind == "ep":
            bvid, cid, title = resolve_episode(self.session, raw_id)
            label, platform, eptype = f"ep{raw_id}", "番剧", ("ep", raw_id)
        elif kind == "ss":
            bvid, cid, title = resolve_ss(self.session, raw_id)
            label, platform, eptype = f"ss{raw_id}", "番剧合集", ("ss", raw_id)
        elif kind == "md":
            bvid, cid, title = resolve_md(self.session, raw_id)
            label, platform, eptype = f"md{raw_id}", "媒体详情", None
        else:
            raise ValueError(f"未知播放类型: {kind}")
        self._log(f"  [+] {title} (BV={bvid})")
        if eptype:
            self._setup_episodes(*eptype, cid)
        else:
            self._log("  [*] 媒体详情页无 season id，不支持切集连播")
        self._play_dash(platform, label, bvid, cid, title)

    def _play_yt(self, ytid):
        from bili_clipboard_dolby import launch_player
        url = f"https://www.youtube.com/watch?v={ytid}"
        self._log(f"    唤起播放器: {url[:60]}...")
        try:
            launch_player(self.player_path, url, url, log=self._log)
        except Exception as e:
            self._log(f"    [!] YT launch 失败: {e}")

    # ---------- 退出 ----------
    def _quit(self):
        self.running = False
        self.root.destroy()
        sys.exit(0)


def _read_clipboard() -> str:
    """读取剪贴板文本。优先 Win32 API（&lt;1ms），失败则回退 pyperclip。"""
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


if __name__ == "__main__":
    try:
        App()
        tk.mainloop()
    except Exception:
        import traceback
        _log_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "BiliYTPlayer"
        _log_dir.mkdir(parents=True, exist_ok=True)
        log_path = _log_dir / "BiliYTPlayer_error.log"
        with open(log_path, "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        try:
            import tkinter.messagebox as mb
            mb.showerror("启动失败", f"程序启动失败，错误日志已保存到：\n{log_path}")
        except Exception:
            pass
