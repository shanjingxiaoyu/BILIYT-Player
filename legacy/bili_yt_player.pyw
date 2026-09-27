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
from tkinter import ttk, messagebox, filedialog
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


# =============================================================================
# 浏览器配置页（替代 tkinter 弹框）
# =============================================================================

_CONFIG_HTML = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>配置 SESSDATA</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Microsoft YaHei",sans-serif;background:#f0f2f5;display:flex;justify-content:center;align-items:center;min-height:100vh}
.card{background:#fff;border-radius:12px;padding:32px 28px;width:420px;box-shadow:0 4px 24px rgba(0,0,0,.08)}
h2{font-size:18px;margin-bottom:6px;color:#1a1a1a}
.desc{font-size:13px;color:#666;line-height:1.8;margin-bottom:16px}
.desc b{color:#0078d4}
input{width:100%;padding:10px 12px;font-size:13px;border:1px solid #d0d5dd;border-radius:6px;outline:none;font-family:monospace}
input:focus{border-color:#0078d4;box-shadow:0 0 0 3px rgba(0,120,212,.1)}
.btn{width:100%;padding:10px;margin-top:12px;border:none;border-radius:6px;font-size:14px;font-weight:bold;cursor:pointer;color:#fff;background:#0078d4}
.btn:hover{background:#106ebe}
.msg{margin-top:10px;font-size:12px;text-align:center}
.msg.ok{color:#107c10}
.msg.err{color:#d83b01}
</style></head><body>
<div class="card">
<h2>首次配置 — B 站 SESSDATA</h2>
<div class="desc">
<b>浏览器登录 bilibili.com</b> → F12 → Application → Cookies → <br>
www.bilibili.com → 找到 <b>SESSDATA</b> → 双击 Value 复制 → 粘贴到下方
</div>
<input id="sd" type="password" placeholder="粘贴 SESSDATA 到这里…" autofocus>
<button class="btn" onclick="submit()">保存并启动</button>
<div id="msg" class="msg"></div>
</div>
<script>
async function submit(){
  const val=document.getElementById('sd').value.trim();
  if(!val){document.getElementById('msg').className='msg err';document.getElementById('msg').textContent='请粘贴 SESSDATA';return}
  try{
    const r=await fetch('/save?sessdata='+encodeURIComponent(val));
    if(r.ok){document.getElementById('msg').className='msg ok';document.getElementById('msg').textContent='已保存！窗口即将关闭…';setTimeout(()=>{window.close()},800)}
    else{const t=await r.text();document.getElementById('msg').className='msg err';document.getElementById('msg').textContent='失败: '+t}
  }catch(e){document.getElementById('msg').className='msg err';document.getElementById('msg').textContent='连接失败，请刷新重试'}
}
</script>
</body></html>"""


def _web_sessdata_input(env_path: Path) -> str | None:
    """启动临时 HTTP 服务 → 浏览器打开配置页 → 等待用户提交 → 保存 .env → 返回 SESSDATA。"""
    import webbrowser
    import json
    import socket
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs

    result = {"value": None, "done": False}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/" or path == "/index.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_CONFIG_HTML.encode())
            elif path == "/save":
                qs = parse_qs(urlparse(self.path).query)
                sd = qs.get("sessdata", [""])[0].strip().strip("\"'")
                if sd:
                    env_path.write_text(f"# B 站登录态 Cookie\nSESSDATA={sd}\n", encoding="utf-8")
                    result["value"] = sd
                    result["done"] = True
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"OK")
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"empty")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass  # 静默 HTTP 日志

    # 检测端口可用性，被占用则自动选下一个
    port = 18921
    for _offset in range(10):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            sock.close()
            break
        except OSError:
            port += 1
            sock.close()
    else:
        print("[!] 无法绑定 HTTP 端口 (18921-18930 均被占用)。", flush=True)
        return None

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    webbrowser.open(f"http://127.0.0.1:{port}")
    print(f"[*] 已打开浏览器配置页面 (端口 {port})，请在页面中粘贴 SESSDATA。", flush=True)

    # 轮询等待用户提交,120 秒超时防止无限阻塞
    deadline = time.time() + 120
    while not result["done"]:
        if time.time() > deadline:
            server.shutdown()
            print("[!] SESSDATA 输入超时。", flush=True)
            return None
        time.sleep(0.3)

    server.shutdown()
    print("[+] SESSDATA 已保存。", flush=True)
    return result["value"]


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("剪贴板直连播放器 — B站 / YouTube")
        self.root.geometry("640x600")
        self.root.resizable(True, True)
        self.root.minsize(560, 480)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        self.sessdata = None
        self.running = False
        self.paused = False
        self.history: list[dict] = []
        self.task_queue: queue.Queue = queue.Queue()
        self.recent_ids: deque[str] = deque(maxlen=20)
        self.sponsor_cfg: dict = {}

        if ENV_PATH.exists():
            load_dotenv(ENV_PATH)
            self.sessdata = os.getenv("SESSDATA", "").strip() or None

        # SponsorBlock 配置（缺失时自动生成模板）
        try:
            from sponsorblock import load_config
            self.sponsor_cfg = load_config(_CONFIG_DIR, log=lambda m: print(m, flush=True))
        except Exception as e:
            print(f"[!] SponsorBlock 配置加载失败，功能停用: {e}", flush=True)
            self.sponsor_cfg = {"enabled": False}

        self._build_ui()

        if not self.sessdata:
            # 无 SESSDATA → 浏览器弹 HTML 配置页
            self.sessdata = _web_sessdata_input(ENV_PATH)
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

        # ---- 播放历史（Treeview：支持双击重播）----
        hist_frame = ttk.LabelFrame(self.main_frame, text="播放历史（双击条目重播）", padding=6)
        hist_frame.pack(fill="both", expand=True, pady=(8, 0))

        cols = ("time", "platform", "title", "quality")
        self.hist_tree = ttk.Treeview(
            hist_frame, columns=cols, show="headings", height=6, selectmode="browse",
        )
        for col, text, width in (
            ("time", "时间", 52), ("platform", "来源", 62),
            ("title", "标题", 250), ("quality", "画质/音轨", 120),
        ):
            self.hist_tree.heading(col, text=text)
            self.hist_tree.column(col, width=width, anchor="w",
                                  stretch=(col == "title"))
        vsb = ttk.Scrollbar(hist_frame, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=vsb.set)
        self.hist_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.hist_tree.bind("<Double-1>", self._on_history_double_click)

        # ---- 按钮栏 ----
        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.pack(fill="x", pady=(10, 0))

        self.pause_btn = ttk.Button(btn_frame, text="暂停监听", command=self.toggle_pause)
        self.pause_btn.pack(side="left")

        ttk.Button(btn_frame, text="清空历史", command=self._clear_history).pack(side="left", padx=6)

        ttk.Button(btn_frame, text="退出", command=self._quit).pack(side="right")

        ttk.Button(btn_frame, text="跳过设置…", command=self._open_settings).pack(side="right", padx=6)

        # ---- SponsorBlock 开关 ----
        self.skip_var = tk.BooleanVar(value=bool(self.sponsor_cfg.get("enabled", True)))
        ttk.Checkbutton(
            self.main_frame,
            text="自动跳过 UP 主广告片段 (BilibiliSponsorBlock)",
            variable=self.skip_var,
            command=self._toggle_sponsorblock,
        ).pack(anchor="w", pady=(8, 0))

    # ---------- 跳过设置窗口 ----------
    def _open_settings(self):
        """打开「跳过设置」窗口：按分类选择档位（对齐原插件的配置界面）。"""
        from sponsorblock import (
            ALL_CATEGORIES, CATEGORY_NAMES, CATEGORY_DESCS, CATEGORY_COLORS,
            parse_categories, save_config,
        )

        win = tk.Toplevel(self.root)
        win.title("BilibiliSponsorBlock — 跳过设置")
        win.transient(self.root)
        win.resizable(False, False)
        try:
            win.iconbitmap(default="")
        except Exception:
            pass

        tier_labels = [
            ("auto", "自动跳过"),
            ("manual", "手动跳过"),
            ("bar", "仅在进度条显示"),
            ("prohibited", "禁用"),
        ]
        label_to_tier = {lab: t for t, lab in tier_labels}
        current = parse_categories(self.sponsor_cfg.get("categories", ""))

        ttk.Label(win, text="片段类别与跳过方式",
                  font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        ttk.Label(win,
                  text="「自动跳过」= 进入片段立即跳，3 秒内可按 Enter 撤销；"
                       "「手动跳过」= 进度条显色，按 n 跳过。",
                  wraplength=560, justify="left", foreground="#555",
                  ).pack(anchor="w", padx=14, pady=(0, 8))

        # 表头
        head = ttk.Frame(win); head.pack(fill="x", padx=14)
        ttk.Label(head, text="类别", width=22, font=("Microsoft YaHei", 9, "bold")).pack(side="left")
        ttk.Label(head, text="跳过方式", width=16, font=("Microsoft YaHei", 9, "bold")).pack(side="left")
        ttk.Label(head, text="进度条颜色", font=("Microsoft YaHei", 9, "bold")).pack(side="left")

        rows = {}
        body = ttk.Frame(win); body.pack(fill="x", padx=14, pady=(2, 8))
        for cat in ALL_CATEGORIES:
            row = ttk.Frame(body); row.pack(fill="x", pady=1)
            ttk.Label(row, text=CATEGORY_NAMES.get(cat, cat), width=22).pack(side="left")

            var = tk.StringVar(
                value=dict(tier_labels).get(current.get(cat, "auto"), "自动跳过"))
            combo = ttk.Combobox(row, textvariable=var, width=14, state="readonly",
                                 values=[lab for _, lab in tier_labels])
            combo.pack(side="left")
            rows[cat] = var

            # 颜色块（用 ASS 的 BBGGRR 反解为 RGB 供显示）
            ass = CATEGORY_COLORS.get(cat, "&H00D400&")
            try:
                bb, gg, rr = ass[4:6], ass[6:8], ass[8:10]
                hexc = f"#{rr}{gg}{bb}"
            except Exception:
                hexc = "#808080"
            sw = tk.Label(row, text="  ", bg=hexc, relief="solid", borderwidth=1)
            sw.pack(side="left", padx=(10, 6))
            ttk.Label(row, text=CATEGORY_DESCS.get(cat, ""), foreground="#666",
                      width=46, anchor="w").pack(side="left")

        # 选项
        opt = ttk.LabelFrame(win, text="选项", padding=8)
        opt.pack(fill="x", padx=14, pady=(0, 8))

        notify_var = tk.BooleanVar(value=bool(self.sponsor_cfg.get("notify", True)))
        ttk.Checkbutton(opt, text="跳过时显示提示（含 Enter 撤销倒计时）",
                        variable=notify_var).grid(row=0, column=0, columnspan=2,
                                                  sticky="w", padx=4)

        ttk.Label(opt, text="撤销窗口（秒）").grid(row=1, column=0, sticky="w", padx=4, pady=(6, 0))
        undo_var = tk.StringVar(value=str(self.sponsor_cfg.get("undo_seconds", 3.0)))
        ttk.Spinbox(opt, from_=0, to=30, increment=0.5, width=6,
                    textvariable=undo_var).grid(row=1, column=1, sticky="w", pady=(6, 0))

        ttk.Label(opt, text="最短片段（秒）").grid(row=2, column=0, sticky="w", padx=4, pady=(6, 0))
        min_var = tk.StringVar(value=str(self.sponsor_cfg.get("min_duration", 1.0)))
        ttk.Spinbox(opt, from_=0, to=60, increment=0.5, width=6,
                    textvariable=min_var).grid(row=2, column=1, sticky="w", pady=(6, 0))

        note = ttk.Label(win, text="保存后，播放下一个视频即生效（无需重启）。",
                         foreground="#0a7", wraplength=560, justify="left")
        note.pack(anchor="w", padx=14)

        btns = ttk.Frame(win); btns.pack(fill="x", padx=14, pady=(8, 12))

        def do_save():
            tiers = {cat: label_to_tier.get(var.get(), "auto")
                     for cat, var in rows.items()}
            cfg = dict(self.sponsor_cfg)
            cfg["category_tiers"] = tiers
            cfg["notify"] = bool(notify_var.get())
            try:
                cfg["undo_seconds"] = max(0.0, float(undo_var.get()))
            except ValueError:
                pass
            try:
                cfg["min_duration"] = max(0.0, float(min_var.get()))
            except ValueError:
                pass
            if save_config(_CONFIG_DIR, cfg):
                self.sponsor_cfg = cfg
                note.configure(text="已保存，播放下一个视频即生效。", foreground="#0a7")
                self._log("[*] 跳过设置已保存（下个视频生效）")
                auto_n = sum(1 for t in tiers.values() if t == "auto")
                man_n = sum(1 for t in tiers.values() if t == "manual")
                self._log(f"    自动跳过 {auto_n} 类，手动跳过 {man_n} 类")
                win.after(600, win.destroy)
            else:
                note.configure(text="保存失败，请检查文件权限。", foreground="#c00")

        ttk.Button(btns, text="保存", command=do_save).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="right")

        win.update_idletasks()
        # 居中到主窗口
        try:
            px, py = self.root.winfo_rootx(), self.root.winfo_rooty()
            pw, ph = self.root.winfo_width(), self.root.winfo_height()
            w, h = win.winfo_width(), win.winfo_height()
            win.geometry(f"+{px + max(0, (pw - w) // 2)}+{py + max(0, (ph - h) // 2)}")
        except Exception:
            pass

    def _toggle_sponsorblock(self):
        """切换广告跳过开关并持久化到 sponsorblock.conf。"""
        enabled = bool(self.skip_var.get())
        self.sponsor_cfg["enabled"] = enabled
        try:
            from sponsorblock import save_config
            save_config(_CONFIG_DIR, self.sponsor_cfg)
        except Exception as e:
            self._log(f"[!] 配置保存失败: {e}")
        self._log("[*] 广告跳过已开启。" if enabled else "[*] 广告跳过已关闭。")

    def _fetch_sponsor_segments(self, bvid, cid):
        """查询 SponsorBlock 片段。未启用或失败时返回空列表（绝不影响播放）。"""
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
        """生成历史记录里的跳过概要，例如 '跳过 2 段 0m45s'。"""
        if not segments:
            return ""
        total = sum(max(0.0, s["end"] - s["start"]) for s in segments)
        return f"跳过 {len(segments)} 段 {int(total // 60)}m{int(total % 60)}s"

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

    def _add_history(self, platform, vid, title, quality, audio, raw_id=None):
        """线程安全：后台线程只做数据处理，UI 操作通过 root.after 调度到主线程。

        vid     —— 展示用的标签（如 "ep123" / BV 号）
        raw_id  —— 供重播使用的裸 ID（如 "123"），因为 _process_task 需要纯数字
        """
        ts = datetime.now().strftime("%H:%M")
        entry = {"time": ts, "platform": platform, "vid": vid, "title": title,
                 "detail": f"{quality}  {audio}",
                 "raw_id": str(raw_id if raw_id is not None else vid)}
        self.history.append(entry)
        self.root.after(0, self._add_history_ui, entry)

    def _add_history_ui(self, entry):
        """主线程执行：安全操作 tkinter 控件。"""
        try:
            iid = self.hist_tree.insert(
                "", "end",
                values=(entry["time"], entry["platform"], entry["title"], entry["detail"]),
            )
            self.hist_tree.see(iid)
        except Exception:
            pass

    def _clear_history(self):
        self.history.clear()
        try:
            for iid in self.hist_tree.get_children():
                self.hist_tree.delete(iid)
        except Exception:
            pass

    # ---------- 历史重播 ----------
    def _on_history_double_click(self, event):
        """双击历史条目 → 重新播放该视频。"""
        try:
            iid = self.hist_tree.identify_row(event.y)
            if not iid:
                return
            idx = self.hist_tree.index(iid)
        except Exception:
            return
        if not (0 <= idx < len(self.history)):
            return
        entry = self.history[idx]
        if not getattr(self, "running", False):
            self._log("[!] 尚未就绪，无法重播")
            return
        self._log(f">> 重播历史: {entry['platform']} {entry['vid']}")
        self.task_queue.put((self._type_for(entry["platform"]), entry["raw_id"]))

    @staticmethod
    def _type_for(platform):
        """把历史条目的「来源」映射回任务类型。"""
        return {
            "B站": "bili", "番剧": "ep", "番剧合集": "ss",
            "媒体详情": "md", "YouTube": "yt",
        }.get(platform, "bili")

    # ---------- SESSDATA 输入页 ----------
    def _show_sessdata_page(self):
        win = tk.Toplevel(self.root)
        win.title("首次配置")
        win.geometry("450x280")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", self._quit)

        ttk.Label(
            win, text="首次使用，请输入 B 站 SESSDATA",
            font=("Microsoft YaHei", 11, "bold"),
        ).pack(pady=(15, 5))

        ttk.Label(
            win,
            text=(
                "浏览器登录 bilibili.com\n"
                "按 F12 → Application → Cookies → www.bilibili.com\n"
                "找到 SESSDATA，复制 Value 粘贴到下方"
            ),
            justify="left",
        ).pack(pady=(0, 10))

        entry = ttk.Entry(win, width=50, show="*")
        entry.pack(pady=5)

        def save():
            val = entry.get().strip().strip("\"'")
            if not val:
                messagebox.showwarning("提示", "请输入 SESSDATA")
                return
            ENV_PATH.write_text(
                "# B 站登录态 Cookie\nSESSDATA=" + val + "\n",
                encoding="utf-8",
            )
            self.sessdata = val
            win.destroy()
            self._show_status_page()
            self.start_monitor()

        ttk.Button(win, text="保存并启动", command=save).pack(pady=(10, 5))
        ttk.Button(win, text="退出", command=self._quit).pack()

        self.root.withdraw()
        win.wait_window()
        self.root.deiconify()

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
            title="请手动选择 mpv.exe（通常在 SMPlayer 安装目录的 mpv 子目录下）",
            filetypes=[("mpv", "mpv.exe"), ("SMPlayer", "smplayer.exe"), ("所有文件", "*.*")],
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
            self._log("[*] SESSDATA 已过期，正在打开浏览器重新登录…")
            ENV_PATH.unlink(missing_ok=True)
            self.sessdata = _web_sessdata_input(ENV_PATH)
            if not self.sessdata:
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
        """从剪贴板文本中匹配链接，返回 (type, raw_id) 或 None。"""
        for pattern, type_name in self._LINK_PATTERNS:
            m = pattern.search(text)
            if m:
                return (type_name, m.group(1))
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
                _type, raw_id = result
                dedup_key = f"{_type}:{raw_id}"
                if dedup_key not in self.recent_ids:
                    self.recent_ids.append(dedup_key)
                    self.task_queue.put((_type, raw_id))
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

    # ---------- 播放 ----------
    def _play_bili_stream(self, bvid, cid, title, platform, vid_label, raw_id=None):
        """B 站系（正片 / 番剧 / 合集 / 媒体页）统一播放流程。

        四个入口原先各自复制了一份相同的 get_playurl → pick_dolby_streams →
        launch_player 代码，这里合并为一处，并注入 SponsorBlock 片段。
        raw_id 供历史重播使用（ep/ss/md 需要裸数字）。
        """
        from bili_clipboard_dolby import get_playurl, pick_dolby_streams, launch_player

        data = get_playurl(self.session, bvid, cid, self.img_key, self.sub_key)
        dash = data.get("dash")
        if not dash:
            self._log("  [!] 无 DASH 数据")
            return
        vurl, aurl, vd, ad = pick_dolby_streams(dash)
        if not vurl:
            self._log("  [!] 未能提取可播放的视频流")
            return

        segments = self._fetch_sponsor_segments(bvid, cid)
        summary = self._summarize_segments(segments)
        if self.sponsor_cfg.get("enabled", True):
            self._log(f"  [*] SponsorBlock: {'命中 ' + summary if summary else '无片段'}")

        suffix = f"  |  {summary}" if summary else ""
        self._add_history(platform, vid_label, title, vd,
                          (ad or "普通音频") + suffix, raw_id=raw_id)

        cfg = dict(self.sponsor_cfg)
        cfg["bvid"] = bvid
        cfg["cid"] = cid
        launch_player(
            self.player_path, vurl, title,
            audio_url=aurl, sessdata=self.sessdata, log=self._log,
            segments=segments, sponsor_cfg=cfg,
        )

    def _play_bili(self, bvid):
        from bili_clipboard_dolby import get_cid
        cid, title = get_cid(self.session, bvid)
        self._play_bili_stream(bvid, cid, title, "B站", bvid)

    def _play_episode(self, ep_id: int):
        from bili_clipboard_dolby import resolve_episode
        bvid, cid, full_title = resolve_episode(self.session, ep_id)
        self._log(f"  [+] {full_title} (BV={bvid})")
        self._play_bili_stream(bvid, cid, full_title, "番剧", f"ep{ep_id}", raw_id=ep_id)

    def _play_yt(self, ytid):
        from bili_clipboard_dolby import launch_player
        url = f"https://www.youtube.com/watch?v={ytid}"
        self._log(f"    唤起播放器: {url[:60]}...")
        try:
            launch_player(self.player_path, url, url, log=self._log)
            # 记入历史，使双击可重播（YouTube 无 SponsorBlock 数据）
            self._add_history("YouTube", ytid, url, "yt-dlp 最高画质", "")
        except Exception as e:
            self._log(f"    [!] YT launch 失败: {e}")

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
