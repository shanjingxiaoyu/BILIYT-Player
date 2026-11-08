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
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime
import subprocess as sp
from dotenv import load_dotenv
import app_log  # 本地开发日志：源码运行写盘，打包 exe 自动静默

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
YT_RE = re.compile(r"(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)([a-zA-Z0-9_-]{11})")
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
        self.task_queue: queue.Queue = queue.Queue()
        self.recent_ids: deque[str] = deque(maxlen=20)

        if ENV_PATH.exists():
            load_dotenv(ENV_PATH)
            self.sessdata = os.getenv("SESSDATA", "").strip() or None

        self._build_ui()

        if not self.sessdata:
            # 无 SESSDATA → 弹窗扫码登录（阻塞主线程直到完成/取消/失败）
            # B-2（2026-10-27 修）：失败/取消都不再静默退出——说明原因 + 给重试。
            # 原来这里拿到 None 就直接 sys.exit(0)，用户看到的是"程序一闪就没"。
            self.sessdata, login_err = self._qr_login_window()
            while not self.sessdata:
                if login_err:
                    retry = messagebox.askretrycancel(
                        "B 站登录失败",
                        f"{login_err}\n\n（常见原因：网络不通、B 站接口暂时不可用）\n\n是否重试？")
                else:
                    retry = messagebox.askretrycancel(
                        "未登录",
                        "你取消了扫码登录。\n未登录时 B 站链接无法播放。\n\n是否重新扫码？")
                if not retry:
                    self.root.destroy()
                    sys.exit(0)
                self.sessdata, login_err = self._qr_login_window()

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

        self.refresh_btn = ttk.Button(btn_frame, text="刷新监听", command=self.refresh_monitor)
        self.refresh_btn.pack(side="left")

        ttk.Button(btn_frame, text="重新登录", command=self._relogin).pack(side="left", padx=6)

        ttk.Button(btn_frame, text="退出", command=self._quit).pack(side="right")

    def _log(self, msg):
        """线程安全：所有调用方都可能来自后台线程，统一通过 root.after 调度到主线程。"""
        # 落盘放在 UI 调度之前：窗口都已销毁时 after 会失败，但日志仍要留下。
        # app_log 在打包 exe 下自动 no-op，发布版不产生任何日志文件。
        app_log.log(str(msg))
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
        self.root.after(0, self._add_history_ui, line)

    def _add_history_ui(self, line):
        """主线程执行：安全操作 tkinter 控件。"""
        self.hist_text.configure(state="normal")
        self.hist_text.insert("end", line + "\n")
        self.hist_text.see("end")
        self.hist_text.configure(state="disabled")

    def _relogin(self):
        """运行中手动重新登录（换账号/登录态异常时用）。

        弹扫码 → 成功后后台线程热替换会话：不重启应用、不丢剪贴板监听。
        取消或失败则原登录态原样保留。启动时因网络异常没进监听态的，
        重新登录成功后自动恢复监听。
        """
        from bili_clipboard_dolby import init_bili_session
        self._log("[*] 正在打开扫码登录…")
        sd, err = self._qr_login_window()
        if not sd:
            # B-2（2026-10-27 修）：区分"取消"与"失败"，失败要把原因说出来
            self._log(f"[!] 重新登录失败：{err}" if err
                      else "[*] 已取消重新登录，当前登录态保持不变。")
            return
        self._log("[*] 扫码成功，正在验证新登录态…")

        def do_init():
            try:
                session, img_key, sub_key = init_bili_session(sd)
            except Exception as e:
                self._log(f"[!] 新登录态验证失败，继续使用原登录态: {e}")
                return
            self.sessdata = sd
            self.session, self.img_key, self.sub_key = session, img_key, sub_key
            self._log("[+] 重新登录成功，B 站鉴权已切换。")
            # 同步切集上下文：否则换账号后 Ctrl+←→/自动连播仍用旧 session 调 API
            try:
                from bili_clipboard_dolby import _EPISODE_STATE
                ctx = _EPISODE_STATE.get("ctx")
                if ctx:
                    ctx.update(self._episode_ctx())  # 保留 pgc 等既有字段
            except Exception:
                pass
            # 启动时若因网络异常没进入监听态，借此恢复
            if not self.running:
                self.root.after(0, lambda: self.refresh_btn.configure(
                    text="刷新监听", state="normal"))
                self.start_monitor()

        threading.Thread(target=do_init, daemon=True).start()

    # ---------- SESSDATA 扫码登录 ----------
    def _qr_login_window(self) -> tuple[str | None, str | None]:
        """弹窗显示 B 站登录二维码，后台线程轮询扫码。

        返回 (sessdata, error_msg)：
        - 成功:  (SESSDATA, None)
        - 取消:  (None, None)     —— 用户主动点取消/关窗
        - 失败:  (None, 原因)     —— 二维码申请失败/失效/超时等

        线程分工（B-3，2026-10-27 修）：run 线程只往 ui_q 投递纯 Python 数据，
        所有 tkinter 操作由主线程的 drain 代劳——子线程直接调 after 在
        wait_window 期间必抛 "main thread is not in main loop"，
        会让二维码画不出、关窗丢失、wait_window 永久假死。
        B-2：进度与失败消息同时进主窗口日志与二维码窗自己的状态栏，
        不再只写进被模态窗遮挡的主窗口日志。
        """
        from bili_clipboard_dolby import qr_login_sessdata

        result = {"sessdata": None, "error": None, "last_err": None}
        cancel_evt = threading.Event()
        # 子线程 → 主线程的唯一通道：("draw"|"status"|"done", payload)
        ui_q: queue.Queue = queue.Queue()
        cancel_at = {"t": None}

        win = tk.Toplevel(self.root)
        win.title("B 站扫码登录")
        win.geometry("340x440")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        canvas = tk.Canvas(win, width=290, height=290, bg="white", highlightthickness=0)
        canvas.pack(pady=(20, 8))

        status_lbl = ttk.Label(win, text="正在获取二维码…", font=("Microsoft YaHei", 10, "bold"))
        status_lbl.pack(pady=(0, 4))
        ttk.Label(
            win, text="使用 B 站 App「扫一扫」，在手机上确认登录",
            foreground="#888", font=("Microsoft YaHei", 9),
        ).pack(pady=(0, 14))

        def on_cancel():
            cancel_evt.set()
            # 记下取消时刻：线程可能正卡在网络请求里（最长 10s 超时 + 2s 轮询），
            # 不能干等它自然醒，超时后由 drain 强制收尾（B-3：绝不假死）。
            if cancel_at["t"] is None:
                cancel_at["t"] = time.time()
        win.protocol("WM_DELETE_WINDOW", on_cancel)
        ttk.Button(win, text="取消", command=on_cancel).pack(pady=(0, 16))

        # ---- 子线程侧：只碰纯 Python 对象，绝不碰 tkinter ----
        def draw(matrix):
            ui_q.put(("draw", matrix))

        def on_any(msg):
            text = str(msg)
            # B-2：留住最后一条错误，供弹窗说清原因（后端失败时只 return None，
            # 具体原因原本只进日志，而日志正被这个模态窗挡着，等于没说）
            if text.startswith("[!]"):
                result["last_err"] = text
            ui_q.put(("status", text))

        def run():
            try:
                result["sessdata"] = qr_login_sessdata(
                    emit=on_any, draw_qr=draw, cancel=cancel_evt,
                )
            except Exception as e:
                result["error"] = f"扫码过程异常: {e}"
            finally:
                if not result["sessdata"] and not cancel_evt.is_set() \
                        and not result["error"]:
                    result["error"] = result.get("last_err") or "登录未完成"
                ui_q.put(("done", None))  # B-3：任何出口都通知主线程收尾

        # ---- 主线程侧：唯一接触 tkinter 的地方 ----
        # 为什么必须绕这一圈（2026-10-27 实测定论）：
        # _tkinter 的 C 层要求"主线程正处在 main loop 中"才允许注册回调，
        # 而 wait_window 用的是 Tcl 层的 tkwait 命令，C 层 dispatching 标志仍是 0。
        # 于是子线程调 after 会先卡 1 秒再抛
        # "RuntimeError: main thread is not in main loop"。
        # 危害链：二维码画不出（空白框）→ 状态栏卡在"正在获取二维码…" →
        # finally 的关窗 after 同样丢失 → wait_window 永久假死。
        # 该窗口期只存在于冷启动（mainloop 尚未起来），热路径「重新登录」
        # 恰好在 mainloop 内所以一直没暴露。
        def drain():
            done = False
            while True:
                try:
                    kind, payload = ui_q.get_nowait()
                except queue.Empty:
                    break
                if kind == "draw":
                    self._draw_qr_canvas(canvas, payload)
                    status_lbl.configure(text="请用 B 站 App 扫码")
                elif kind == "status":
                    self._log_ui(payload)
                    status_lbl.configure(text=payload[:72])
                elif kind == "done":
                    done = True
            if done:
                win.destroy()
                return
            if cancel_at["t"] is not None and time.time() - cancel_at["t"] > 1.5:
                win.destroy()  # 用户已点取消，线程迟迟不醒就强行收场
                return
            win.after(50, drain)

        threading.Thread(target=run, daemon=True).start()
        win.after(50, drain)
        self.root.wait_window(win)  # 主线程局部事件循环，阻塞至扫码完成/取消/失败
        return result["sessdata"], result["error"]

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

        def run():
            # 后台初始化线程一旦抛非预期异常，原来会静默死掉 →
            # 监听不启动、复制链接毫无反应，且看不到任何原因。落盘兜住它。
            try:
                self._init_backend()
            except Exception:
                app_log.exception("后台初始化线程异常（剪贴板监听可能未启动）")
                self._log("[!] 初始化失败，详见本地开发日志")

        threading.Thread(target=run, daemon=True).start()

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
            self.root.after(0, lambda: self.refresh_btn.configure(state="disabled"))

    def _continue_init(self, player_path):
        """播放器已确定，继续初始化 B 站鉴权和监听。"""
        from bili_clipboard_dolby import init_bili_session, SessdataInvalid

        self.player_path = player_path
        self._log(f"[+] 播放器: {player_path}")

        # ---- B 站鉴权（单次 nav API 完成验证 + WBI 密钥获取） ----
        self._log("[*] B 站鉴权… 连接B站API")

        try:
            self.session, self.img_key, self.sub_key = init_bili_session(self.sessdata)
            self._log("[+] B 站鉴权就绪。")
        except SessdataInvalid as e:
            # 仅"登录态无效"才删 .env 跳扫码；网络异常不删（见 SessdataInvalid 注释）
            self._log(f"[!] B 站鉴权失败: {e}")
            self._log("[*] SESSDATA 已过期，正在弹出扫码登录…")
            ENV_PATH.unlink(missing_ok=True)
            # 弹窗必须在主线程，后台线程等待结果
            done = threading.Event()
            holder = {}

            def do_login():
                holder["sd"], holder["err"] = self._qr_login_window()
                done.set()

            self.root.after(0, do_login)
            done.wait(timeout=240)  # 最多等 4 分钟（二维码有效期 + 轮询超时）
            self.sessdata = holder.get("sd")
            if not self.sessdata:
                self._log(f"[!] 扫码登录未完成: {holder.get('err') or '用户取消'}")
                self.root.after(0, self._quit)
                return
            try:
                self.session, self.img_key, self.sub_key = init_bili_session(self.sessdata)
                self._log("[+] B 站鉴权就绪。")
            except Exception as e2:
                self._log(f"[!] B 站鉴权仍然失败: {e2}")
                self.root.after(0, lambda: self.refresh_btn.configure(state="disabled"))
                return
        except Exception as e:
            # 网络类异常：保留 .env（登录态还有效），B 站暂不可用，YouTube 不受影响
            self._log(f"[!] B 站 API 网络异常，登录态已保留: {e}")
            self._log("[*] 检查网络后重启即可；B 站链接暂不可播放，YouTube 不受影响。")
            self.root.after(0, lambda: self.refresh_btn.configure(state="disabled"))
            return

        self._log("[*] 监听中 — 复制 B 站 / YouTube 链接即可播放。")
        self.root.after(0, lambda: self.refresh_btn.configure(text="刷新监听", state="normal"))
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
        app_log.log("[+] 剪贴板监听已启动（初始化完成）")
        # 监听线程：轻量，只读剪贴板 + 正则匹配 → 入队
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        # 工作线程：从队列取任务，做 API 调用 + 启动播放器
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def refresh_monitor(self):
        """清空去重记录：之前复制过的链接可被重新识别播放。"""
        self.recent_ids.clear()
        self._log("[*] 已刷新监听：历史记录已清空，剪贴板上的链接可重新识别。")

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
            # 起播链路失败时，GUI 原来只显示一句 str(e)，看不到是哪一步炸的。
            # 落盘带上完整 traceback（发布版 no-op）。
            app_log.exception(f"处理任务失败 type={_type} raw_id={raw_id} extra={extra}")
            self._log(f"  [!] {e}")

    # ---------- 播放 ----------
    def _episode_ctx(self) -> dict:
        """切集线程所需的播放上下文（session + WBI 密钥 + 登录态）。"""
        return {
            "session": self.session, "img_key": self.img_key,
            "sub_key": self.sub_key, "sessdata": self.sessdata,
        }

    def _setup_episodes(self, source_type, source_id, cid, pgc=False):
        """获取剧集列表并写入切集状态（Ctrl+←→ 切集 + 播完自动连播）。"""
        from bili_clipboard_dolby import fetch_episode_list, set_episode_state
        try:
            eps = fetch_episode_list(self.session, source_type, source_id)
            index = next((i for i, e in enumerate(eps) if e.get("cid") == cid), 0)
            ctx = self._episode_ctx()
            ctx["pgc"] = pgc  # 番剧切集走 PGC API，普通 BV 走视频 API
            set_episode_state(eps, index, ctx)
            self._log(f"  [*] 剧集列表 {len(eps)} 集，当前第 {index + 1} 集")
        except Exception as e:
            self._log(f"  [!] 剧集列表获取失败，自动连播不可用: {e}")

    def _play_dash(self, platform, vid_label, bvid, cid, title, pgc=False):
        """公共播放链路：取流 → 选流 → 记历史 → 唤起 mpv。

        pgc=True（番剧 ep/ss/md）走 PGC 专用 API——视频 API 对番剧返回 -404。
        """
        from bili_clipboard_dolby import (get_playurl, get_playurl_pgc,
                                          pick_dolby_streams,
                                          pick_streams_from_result, launch_player)
        if pgc:
            result = get_playurl_pgc(self.session, bvid, cid)
            vurl, aurl, vd, ad = pick_streams_from_result(result)
        else:
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
            self._setup_episodes(*eptype, cid, pgc=(kind != "bili"))
        else:
            self._log("  [*] 媒体详情页无 season id，不支持切集连播")
        self._play_dash(platform, label, bvid, cid, title, pgc=(kind != "bili"))

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
            # 64 位下句柄/指针可能超 32 位：不设 restype 会被 ctypes 截断成 int，
            # GlobalLock 拿到截断指针 → wstring_at 访问冲突 → 静默回退 pyperclip，
            # 快路径形同虚设（2026-10-23 审查发现）。
            user32.GetClipboardData.restype = ctypes.c_void_p
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
            kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
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


def _ensure_single_instance():
    """单实例守卫：已有实例在运行时，把它提到前台并退出当前进程。

    防止双击/三击启动出多个实例——多个实例会读到同一条剪贴板链接，
    各自唤起一个 mpv 重复播放。
    """
    if sys.platform != "win32":
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    # 句柄存到全局，随进程生命周期存在；进程退出后由系统回收
    global _INSTANCE_MUTEX
    _INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, "Local\\BiliYTPlayer_SingleInstance")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "剪贴板直连播放器 — B站 / YouTube")
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        sys.exit(0)


_INSTANCE_MUTEX = None


if __name__ == "__main__":
    app_log.log("=" * 64)
    app_log.log(f"=== 启动 frozen={getattr(sys, 'frozen', False)} "
                f"python={sys.version.split()[0]} ===")
    _ensure_single_instance()
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
