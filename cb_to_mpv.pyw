#!/usr/bin/env python3
"""
cb_to_mpv.pyw  —  剪贴板监听 → mpv 播放

Windows 下无控制台黑框，只有一个主窗口显示状态日志与播放历史。
复制 B 站 / YouTube 链接，自动拉起 mpv；关闭窗口即退出监听程序。
一切解析（WBI 签名 / DASH 分轨 / yt-dlp）交给 mpv 内置机制。

除二维码点阵生成用 qrcode 之外，只用 Python 标准库（含 tkinter）。

便携模式：
  - 同级目录的 mpv.exe 优先
  - 其次查找 mpv/mpv.exe
  - 可通过 config.ini 手动指定路径
  - cookies.txt 不存在时自动创建空白文件
"""

import configparser
import ctypes
import ctypes.wintypes
import io
import json
import os
import queue
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from tkinter import ttk

# ============================================================================
# 路径工具
# ============================================================================

def _get_script_dir() -> Path:
    """获取脚本/EXE 所在目录（PyInstaller 和直接运行均适用）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


SCRIPT_DIR = _get_script_dir()

# ============================================================================
# 日志
# ============================================================================
# 打包后 console=False，sys.stdout 为 None，print 会被静默丢弃。
# "复制没反应"这类故障必须落盘才能排查，故同时写文件。
# 每次启动覆盖（"w"）：只保留本次运行，日志不会无限增长。

LOG_PATH = SCRIPT_DIR / "cb_to_mpv.log"
_log_fh = None

# 每条日志同时送进主窗口的「状态」区。写日志的是后台线程，而 tkinter 的控件
# 只能在主线程操作，所以这里只入队，由主线程的 after 定时器取走。
_ui_on = False
_ui_q = queue.Queue()


def _ui_push(msg: str, kind: str = "status"):
    if _ui_on:
        _ui_q.put((msg, kind))


def _log(msg: str):
    print(msg)
    _ui_push(msg)
    global _log_fh
    try:
        if _log_fh is None:
            _log_fh = open(LOG_PATH, "w", encoding="utf-8")
        _log_fh.write(msg + "\n")
        _log_fh.flush()
    except OSError:
        pass


# ============================================================================
# 配置加载
# ============================================================================

CONFIG_PATH = SCRIPT_DIR / "config.ini"
_cfg = configparser.ConfigParser()

def _load_config():
    """加载 config.ini（可选）。"""
    if CONFIG_PATH.is_file():
        _cfg.read(CONFIG_PATH, encoding="utf-8-sig")

_load_config()

def _cfg_get(section: str, key: str, default: str = "") -> str:
    return _cfg.get(section, key, fallback=default)

# ============================================================================
# MPV 路径解析（自动探测 + config.ini 兜底）
# ============================================================================

def _resolve_mpv() -> str:
    """多级探测 mpv.exe：
       1. 同级目录 mpv-portable/mpv.exe
       2. 同级目录 mpv.exe
       3. 子目录 mpv/mpv.exe
       4. config.ini [paths] mpv_path
       5. 系统 PATH
    """
    # 1. mpv-portable（最常见）
    p = SCRIPT_DIR / "mpv-portable" / "mpv.exe"
    if p.is_file():
        return str(p)

    # 2. 同级
    p = SCRIPT_DIR / "mpv.exe"
    if p.is_file():
        return str(p)

    # 3. 子目录
    p = SCRIPT_DIR / "mpv" / "mpv.exe"
    if p.is_file():
        return str(p)

    # 4. config.ini
    val = _cfg_get("paths", "mpv_path")
    if val and Path(val).is_file():
        return val

    # 5. PATH
    return "mpv"


# ============================================================================
# yt-dlp 路径解析
# ============================================================================

def _resolve_ytdlp() -> str:
    """探测 yt-dlp.exe（mpv ytdl_hook 需要）。"""
    # 同级
    p = SCRIPT_DIR / "yt-dlp.exe"
    if p.is_file():
        return str(p)
    # 子目录 mpv-portable
    p = SCRIPT_DIR / "mpv-portable" / "yt-dlp.exe"
    if p.is_file():
        return str(p)
    # config.ini
    val = _cfg_get("paths", "ytdlp_path")
    if val and Path(val).is_file():
        return val
    return ""


# ============================================================================
# mpv 配置目录
# ============================================================================

def _resolve_mpv_confdir() -> str:
    """探测 mpv 便携配置目录（mpv.conf / input.conf）。"""
    # portable_config
    p = SCRIPT_DIR / "portable_config"
    if p.is_dir():
        return str(p)
    # mpv-portable/portable_config
    p = SCRIPT_DIR / "mpv-portable" / "portable_config"
    if p.is_dir():
        return str(p)
    return ""


# ============================================================================
# Cookies 文件处理
# ============================================================================

def _cookies_path() -> Path:
    """cookies.txt 的目标路径，config.ini 可覆盖。

    扫码登录写文件必须走同一个解析结果，否则会写成另一份没人读的文件 ——
    症状是「扫完码画质照旧」，最难查。"""
    val = _cfg_get("paths", "cookies_file")
    return Path(val) if val else SCRIPT_DIR / "cookies.txt"


def _resolve_cookies() -> str:
    """获取 cookies.txt 路径。不存在则自动创建带说明的空白文件。"""
    p = _cookies_path()

    if not p.is_file():
        p.write_text(
            "# B 站 / YouTube Cookie 文件\n"
            "#\n"
            "# 方法一（推荐）：点主窗口的「扫码登录 B 站」，本文件会被自动写好\n"
            "# 方法二：在 config.ini 的 [cookies] 段设置 browser_cookie = edge\n"
            "# 方法三：安装插件 Get cookies.txt LOCALLY，导出内容粘贴到此文件\n"
            "#\n"
            "# 留空则使用游客模式（B 站 1080P / YouTube 普通画质）。\n"
            "#\n"
            ,
            encoding="utf-8",
        )
        return ""  # 空白文件不传入 mpv

    # 只有注释行 = 用户还没填入真实 Cookie。
    # 不能判首字符：Netscape 格式规定第一行必须是 "# Netscape HTTP Cookie File"
    # 注释，用 startswith("#") 会把每一份正常导出的文件都误判成空白模板。
    if not any(ln.strip() and not ln.lstrip().startswith("#")
               for ln in p.read_text(encoding="utf-8-sig").splitlines()):
        return ""

    return str(p)


def _resolve_browser_cookie() -> str:
    """从 config.ini 读取浏览器名称（edge / chrome / firefox 等）。"""
    return _cfg_get("cookies", "browser_cookie")


# ============================================================================
# Cookie 有效性探测
#
# 存在的理由：Cookie 失效时 yt-dlp 只会静默退回游客模式，1080P 封顶，
# 而 mpv 是被 DEVNULL 拉起的，错误传不回日志 —— 用户只会觉得「画质上不去」。
# 以下所有输出都不含 Cookie 值，只含字段名与过期时间。
# ============================================================================

_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

# Chromium 系浏览器的 Cookie 库位置（相对 %LOCALAPPDATA%）。
# 只用于统计加密代次，不做任何解密。
_BROWSER_COOKIE_DB = {
    "chrome":   "Google/Chrome/User Data/Default/Network/Cookies",
    "chromium": "Chromium/User Data/Default/Network/Cookies",
    "edge":     "Microsoft/Edge/User Data/Default/Network/Cookies",
    "brave":    "BraveSoftware/Brave-Browser/User Data/Default/Network/Cookies",
    "vivaldi":  "Vivaldi/User Data/Default/Network/Cookies",
    "opera":    "Opera Software/Opera Stable/Network/Cookies",
}


def _query_nav(cookie_header: str):
    """请求 nav 接口，返回解析后的 JSON；网络失败返回 None。"""
    headers = {"User-Agent": "Mozilla/5.0",
               "Referer": "https://www.bilibili.com/"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    req = urllib.request.Request(_NAV_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        _log(f"[!] Cookie 探测：请求失败（{type(e).__name__}），"
             f"本次无法确认有效性 —— 不影响播放")
        return None


def _parse_cookies_txt(path: str):
    """解析 Netscape cookies.txt。返回 (B 站条目, 坏行数)，条目为 (名, 过期, 值)。
    不用 MozillaCookieJar：它要求首行是 magic 注释，手工粘贴的文件常常没有。"""
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError as e:
        _log(f"[!] Cookie 探测：读不到文件（{e}）")
        return None, 0
    entries, bad = [], 0
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        f = ln.split("\t")
        if len(f) < 7:
            bad += 1
            continue
        if "bilibili.com" not in f[0]:
            continue
        try:
            exp = int(f[4])
        except ValueError:
            exp = 0          # 非数字过期列按会话 Cookie 处理
        entries.append((f[5], exp, f[6]))
    return entries, bad


def _probe_cookies_file(path: str):
    """先本地看过期（零网络开销），再打一次 nav 让服务端裁决。"""
    entries, bad = _parse_cookies_txt(path)
    if entries is None:
        return
    if bad:
        _log(f"[!] Cookie 探测：{bad} 行格式不符（应为 7 列制表符分隔），已忽略")
    if not entries:
        _log("[!] Cookie 探测：文件里没有 bilibili.com 的条目 —— "
             "B 站按游客模式处理（1080P 封顶）")
        return

    now = int(time.time())
    live = [(n, v) for n, e, v in entries if e == 0 or e > now]
    if not live:
        days = (now - max(e for _, e, _ in entries)) // 86400
        _log(f"[!] Cookie 探测：B 站 Cookie 已过期 {days} 天 —— "
             f"退回游客模式（1080P 封顶），请重新导出 cookies.txt")
        return

    if "SESSDATA" not in [n for n, _ in live]:
        _log("[!] Cookie 探测：有 bilibili.com 条目但缺 SESSDATA —— "
             "B 站登录态很可能无效")

    result = _query_nav("; ".join(f"{n}={v}" for n, v in live))
    if result is None:
        return
    data = result.get("data") or {}
    if result.get("code") == 0 and data.get("isLogin"):
        extra = ""
        if "vipStatus" in data:
            extra = "，大会员" if data["vipStatus"] == 1 else "，非大会员"
        _log(f"[+] Cookie 探测：B 站已登录（mid={data.get('mid')}{extra}）")
    else:
        _log(f"[!] Cookie 探测：服务端判定未登录（code={result.get('code')}）—— "
             f"Cookie 已失效，B 站退回游客模式（1080P 封顶），请重新导出 cookies.txt")


def _read_browser_cookie_rows(db: Path, name: str):
    """只读打开浏览器的 Cookie 库，返回 (rows, 失败原因)。

    immutable=1 让 SQLite 跳过自己的锁协议，但绕不过 Windows 的共享模式：
    浏览器运行时会以拒绝读取的方式独占该文件，而 SQLite 只会回一句
    「unable to open database file」，不带 Win32 错误码。所以失败后再用一次
    裸 open 把真实 errno 分出来，好给出能照做的提示。"""
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro&immutable=1", uri=True)
        try:
            q = ("SELECT encrypted_value FROM cookies "
                 "WHERE host_key LIKE '%bilibili.com'")
            return con.execute(q).fetchall(), None
        finally:
            con.close()
    except sqlite3.Error as e:
        sql_err = f"{type(e).__name__}: {str(e)[:80]}"
        try:
            with open(db, "rb"):
                pass
        except PermissionError:
            return None, (f"{name} 正在运行并独占 Cookie 库（Windows 拒绝共享读取）—— "
                          f"请完全退出 {name} 后重启本程序；"
                          f"yt-dlp 的 cookies-from-browser 靠复制文件，同样会被挡住")
        except OSError as e2:
            return None, f"读不到 {name} 的 Cookie 库（{type(e2).__name__}: {e2}）"
        return None, f"读不到 {name} 的 Cookie 库（{sql_err}）"


def _probe_browser_cookie(browser: str):
    """cookies-from-browser 的解密由 yt-dlp 完成，这里无法复现。
    但有一类必然失败的情况可本地判定：Chromium 的 App-Bound Encryption
    （Cookie 值以 v20 开头），而 yt-dlp 只实现了 v10 分支。
    只统计 3 字节前缀，不解密、不读值。"""
    rel = _BROWSER_COOKIE_DB.get(browser.strip().lower())
    if not rel:
        known = browser.strip().lower() == "firefox"
        _log(f"[*] Cookie 探测：{browser.strip()} 无法本地校验"
             f"{'（Firefox 由 yt-dlp 直接解密，不受 App-Bound Encryption 影响）' if known else '（未识别的浏览器名）'}，"
             f"是否生效取决于 yt-dlp")
        return
    name = browser.strip()
    db = Path(os.environ.get("LOCALAPPDATA", "")) / rel
    if not db.is_file():
        _log(f"[!] Cookie 探测：找不到 {name} 的 Cookie 库，配置可能无效：{db}")
        return
    rows, err = _read_browser_cookie_rows(db, name)
    if err:
        _log(f"[!] Cookie 探测：{err}")
        return

    if not rows:
        _log(f"[!] Cookie 探测：{name} 里没有 bilibili.com 的 Cookie —— "
             f"该浏览器未登录 B 站")
        return
    counts = {}
    for (blob,) in rows:
        p = bytes(blob[:3]).decode("latin1", "replace") if blob else "(空)"
        counts[p] = counts.get(p, 0) + 1
    v20, total = counts.get("v20", 0), len(rows)
    if v20 == total:
        _log(f"[!] Cookie 探测：{name} 的 {total} 条 B 站 Cookie 全部是 v20"
             f"（App-Bound Encryption），yt-dlp 只支持 v10 —— "
             f"cookies-from-browser 会静默失败并退回游客模式。"
             f"请改用 cookies.txt 手动导出")
    elif v20:
        _log(f"[!] Cookie 探测：{name} 的 B 站 Cookie 有 {v20}/{total} 条是 v20"
             f"（App-Bound Encryption），这部分 yt-dlp 解不出来")
    else:
        _log(f"[+] Cookie 探测：{name} 的 {total} 条 B 站 Cookie 均为 v10，"
             f"yt-dlp 可解密（前提是 {name} 已完全退出，"
             f"否则 yt-dlp 复制文件时同样会被独占挡住）")


# ============================================================================
# B 站扫码登录
#
# 用的就是网页版那套登录，只是我们走到 Cookie 就停：generate 登记一个待授权
# 会话（此刻手里没有任何凭证）→ 手机 App 拿自己的登录态确认 → poll 响应的
# Set-Cookie 里服务端签发一份全新会话 Cookie。
#
# 落盘必须是 Netscape cookies.txt：本项目的鉴权入口只有这一条
# （_resolve_cookies → --ytdl-raw-options=cookies=），mpv 和 yt-dlp 都不认 .env。
# 日志里只出现字段名，不出现值 —— SESSDATA 是账号级会话凭证。
# ============================================================================

_QR_GENERATE = ("https://passport.bilibili.com/x/passport-login/"
                "web/qrcode/generate?source=main-fe-header")
_QR_POLL = ("https://passport.bilibili.com/x/passport-login/"
             "web/qrcode/poll?qrcode_key={}")
_QR_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
               "Referer": "https://www.bilibili.com/"}
_QR_SCANNED = 86090     # 已扫码、待手机确认（86101 是未扫码，继续轮询不刷屏）
_QR_EXPIRED = 86038     # 二维码失效


def _qr_get(url: str, cookie: str = ""):
    """GET 一次，返回 (JSON, [Set-Cookie 行])。

    Set-Cookie 必须用 get_all 取：一次响应里有多条同名头，按 dict 读只会剩最后一条。"""
    headers = dict(_QR_HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return (json.loads(resp.read().decode("utf-8")),
                resp.headers.get_all("Set-Cookie") or [])


def _set_cookie_tuple(line: str):
    """一条 Set-Cookie → (名, 值, 到期 epoch)。没有 Max-Age/Expires 就按会话 Cookie（0）。"""
    head, _, rest = line.partition(";")
    name, _, value = head.partition("=")
    name, value = name.strip(), value.strip()
    for attr in (a.strip().lower() for a in rest.split(";")):
        if attr.startswith("max-age="):
            try:
                return name, value, int(time.time()) + int(attr[8:])
            except ValueError:
                break
        if attr.startswith("expires="):
            try:
                return name, value, int(
                    parsedate_to_datetime(attr[8:].strip()).timestamp())
            except ValueError:
                break
    return name, value, 0


def _merge_write_cookies(path: Path, pairs) -> int:
    """把扫码拿到的 Cookie 合并写进 cookies.txt，同名覆盖、其余保留，返回条目数。

    不能整份重写：yt-dlp 会往同一份文件里回写它攒下的 B 站反爬 Cookie
    （buvid3 / b_nut / sid），实测跑一次解析文件就被它改过。覆盖等于把这些
    服务端认可的指纹丢掉，换来的可能是风控或者解析失败。"""
    kept = []
    if path.is_file():
        for ln in path.read_text(encoding="utf-8-sig").splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            f = s.split("\t")
            if len(f) >= 7:
                kept.append(f)

    names = {n for n, _, _ in pairs}
    rows = [f for f in kept if f[5] not in names]
    rows += [[".bilibili.com", "TRUE", "/", "TRUE", str(exp), n, v]
             for n, v, exp in pairs]
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        "# 由 CB-to-MPV 扫码登录写入，yt-dlp 播放时也会回写本文件。\n"
        "# 含登录凭证（等同于你的登录态），请勿分享、勿提交到仓库。\n\n"
        + "\n".join("\t".join(r) for r in rows) + "\n",
        encoding="utf-8")
    return len(rows)


def _qr_draw_matrix(url: str):
    """把待授权 URL 编成二维码点阵。只用 get_matrix()，因此不需要 PIL。"""
    import qrcode
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.get_matrix()


def _qr_login(path: Path, cancel, draw_qr=None, on_status=None,
              timeout: float = 180) -> bool:
    """完整扫码流程；成功则写入 cookies.txt。必须在后台线程调用。

    draw_qr / on_status 由界面层传入，且自己负责切回主线程。"""
    def say(msg: str):
        _log(msg)
        if on_status:
            on_status(msg)

    try:
        gen, gen_cookies = _qr_get(_QR_GENERATE)
    except Exception as e:
        say(f"[!] 二维码申请失败：{type(e).__name__}: {e}")
        return False
    if gen.get("code") != 0:
        say(f"[!] 二维码申请失败：{gen.get('message')}")
        return False

    data = gen.get("data") or {}
    if not data.get("url") or not data.get("qrcode_key"):
        say("[!] B 站返回的二维码信息不完整，请稍后重试。")
        return False
    # 轮询要带上 generate 下发的 Cookie（buvid3 等），只认 qrcode_key 会被当成异常客户端
    cookie = "; ".join(f"{n}={v}" for n, v, _ in
                       (_set_cookie_tuple(c) for c in gen_cookies))

    if draw_qr:
        try:
            draw_qr(_qr_draw_matrix(data["url"]))
        except ImportError:
            say("[!] 缺少 qrcode 库，无法绘制二维码")
            return False
        except Exception as e:
            say(f"[!] 二维码绘制失败：{e}")
            return False

    deadline = time.time() + timeout
    told_scanned = False
    while time.time() < deadline:
        if cancel is not None and cancel.is_set():
            _log("[*] 已取消扫码登录。")
            return False
        try:
            poll, poll_cookies = _qr_get(_QR_POLL.format(data["qrcode_key"]), cookie)
        except Exception as e:
            _log(f"[*] 轮询失败（{type(e).__name__}），稍后重试…")
            time.sleep(2)
            continue

        code = (poll.get("data") or {}).get("code", -1)
        if code == 0:
            pairs = [t for t in (_set_cookie_tuple(c) for c in poll_cookies) if t[0]]
            if not any(n == "SESSDATA" for n, _, _ in pairs):
                say("[!] 服务端说登录成功，但没下发 SESSDATA —— 请重试一次。")
                return False
            total = _merge_write_cookies(path, pairs)
            _log(f"[+] 登录成功，cookies.txt 已写入 {total} 条："
                 f"{', '.join(n for n, _, _ in pairs)}")
            _log("[*] 下一个复制的链接就按登录态解析，不用重启。")
            if on_status:
                on_status("登录成功")
            return True
        if code == _QR_EXPIRED:
            say("[!] 二维码已失效，请重新点「扫码登录 B 站」。")
            return False
        if code == _QR_SCANNED and not told_scanned:
            told_scanned = True
            if on_status:
                on_status("已扫码，请在手机上确认")
        time.sleep(2)

    say("[!] 扫码超时，未完成登录。")
    return False


# ============================================================================
# 登录态监控
#
# Cookie 失效时 yt-dlp 只是静默退回游客模式，而 mpv 是被 DEVNULL 拉起的，
# 错误传不回本进程 —— 用户看到的只是「画质莫名上不去」。所以主动定时问 nav，
# 判定失效就把扫码窗口推出来。
# ============================================================================

AUTH_CHECK_SECS = 1800     # 半小时一次，每次一个 nav 请求，开销可忽略


def _nav_verdict(cookies_file: str):
    """给监控线程用的精简判定：(state, detail)，state ∈ ok/expired/absent/network。
    与 _probe_cookies_file 共用同一批原语，只是那个负责给人看，这个负责触发重新扫码。"""
    if not cookies_file:
        return "absent", "未配置 Cookie"
    entries, _bad = _parse_cookies_txt(cookies_file)
    if not entries:
        return "absent", "文件里没有 B 站条目"

    now = int(time.time())
    live = [(n, v) for n, e, v in entries if e == 0 or e > now]
    if not live:
        return "expired", "Cookie 本地已过期"
    if "SESSDATA" not in [n for n, _ in live]:
        return "expired", "缺 SESSDATA"

    result = _query_nav("; ".join(f"{n}={v}" for n, v in live))
    if result is None:
        return "network", "nav 请求失败"
    data = result.get("data") or {}
    if result.get("code") == 0 and data.get("isLogin"):
        return "ok", f"mid={data.get('mid')}"
    return "expired", f"服务端 code={result.get('code')}"


def _watch_auth():
    """登录态失效检测。只在「登录过 → 现在失效」时弹码：从未配置 Cookie 是用户
    自己选的游客模式，不该开机就弹窗逼登录。"""
    logged_in = _nav_verdict(_resolve_cookies())[0] == "ok"
    while not _stopping.wait(AUTH_CHECK_SECS):
        state, detail = _nav_verdict(_resolve_cookies())
        if state == "network":
            continue          # 断网不等于失效，这时候弹码只会更烦
        if state == "ok":
            logged_in = True
            continue
        if logged_in:
            logged_in = False
            _log(f"[!] 登录态已失效（{detail}），B 站退回游客模式（1080P 封顶）。")
            _ui_push("login", "login")


# ============================================================================
# 代理
# ============================================================================

def _resolve_proxy() -> str:
    """从 config.ini 读取代理。"""
    return _cfg_get("network", "proxy")


def _resolve_autostart() -> bool:
    """从 config.ini 读取开机自启设置。"""
    return _cfg_get("app", "autostart").strip().lower() in ("true", "1", "yes")


def _vbs_str(value) -> str:
    """VBS 字符串字面量转义：双引号翻倍。"""
    return str(value).replace('"', '""')


def _create_shortcut(lnk_path: Path, target: str, args: str = "",
                     workdir: str = "") -> bool:
    """用 WScript.Shell 创建 .lnk（纯标准库，不依赖 pywin32）。

    VBS 以 UTF-16 写入：cscript 靠 BOM 识别编码，路径含中文时不会乱码。
    """
    lines = [
        'Set s = WScript.CreateObject("WScript.Shell")',
        f'Set k = s.CreateShortcut("{_vbs_str(lnk_path)}")',
        f'k.TargetPath = "{_vbs_str(target)}"',
    ]
    if args:
        lines.append(f'k.Arguments = "{_vbs_str(args)}"')
    if workdir:
        lines.append(f'k.WorkingDirectory = "{_vbs_str(workdir)}"')
    lines.append("k.Save")

    vbs_path = Path(tempfile.gettempdir()) / "cb_to_mpv_autostart.vbs"
    try:
        vbs_path.write_text("\r\n".join(lines), encoding="utf-16")
        r = subprocess.run(["cscript", "//Nologo", str(vbs_path)],
                           capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0:
            _log("[!] 创建快捷方式失败: "
                 + r.stderr.decode("mbcs", "ignore").strip())
            return False
        return lnk_path.is_file()
    except Exception as e:
        _log(f"[!] 创建快捷方式失败: {e}")
        return False
    finally:
        vbs_path.unlink(missing_ok=True)


def _setup_autostart(enable: bool):
    """开关机自启快捷方式。仅在状态需要变更时写盘，避免每次启动都拉起 cscript。"""
    startup = (Path(os.environ.get("APPDATA", ""))
               / "Microsoft/Windows/Start Menu/Programs/Startup")
    lnk = startup / "CB-to-MPV.lnk"

    if not enable:
        if lnk.is_file():
            lnk.unlink(missing_ok=True)
            _log("[*] 开机自启已关闭")
        return

    if lnk.is_file():
        return
    if not startup.is_dir():
        _log(f"[!] 启动文件夹不存在，跳过开机自启: {startup}")
        return

    # 打包后指向 exe 自身；源码运行指向当前解释器 + 脚本路径
    if getattr(sys, "frozen", False):
        target, args = sys.executable, ""
    else:
        target = sys.executable
        args = f'"{Path(__file__).resolve()}"'

    if _create_shortcut(lnk, target, args, str(SCRIPT_DIR)):
        _log(f"[+] 开机自启已启用: {target} {args}".rstrip())


# ============================================================================
# 链接正则
# ============================================================================

# yt-dlp 只认 www.bilibili.com，故 m. 域名与裸 BV 号都要在本层归一化。
# 只收录「本身就是一段待播音视频」的地址；UP 主主页(space/…/video)与
# 动态(t.bilibili.com / opus)不收——复制它们的意图通常是分享，不是播放。
BILI_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?bilibili\.com/"
    r"(?:video/BV[a-zA-Z0-9]{10,}"
    r"|bangumi/play/(?:ep|ss)\d+"
    r"|bangumi/media/md\d+"
    r"|cheese/play/(?:ep|ss)\d+"
    r"|audio/au\d+"
    r"|medialist/(?:detail|play)/ml\d+"
    r"|list/[A-Za-z0-9_]+"
    r"|watchlater)"
)

# 个人空间下的合集 / 收藏夹：标识在查询串里（sid= / fid=），不能被剥掉
BILI_SPACE_RE = re.compile(
    r"(?:https?://)?space\.bilibili\.com/\d+/"
    r"(?:lists/\d+|favlist/?\?fid=\d+)"
)

BILI_LIVE_RE = re.compile(r"(?:https?://)?live\.bilibili\.com/(?:blanc/)?\d+")

# 手机 App 分享默认给 b23.tv 短链，yt-dlp 不认，需自行跟随一次重定向
B23_RE = re.compile(r"(?:https?://)?b23\.tv/[A-Za-z0-9]+")

BV_RE = re.compile(r"BV[a-zA-Z0-9]{10,}")

YT_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([a-zA-Z0-9_-]{11})"
)


# ============================================================================
# Win32 剪贴板读取
# ============================================================================

CF_TEXT = 1
CF_UNICODETEXT = 13

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# x64 下 HANDLE/指针必须显式声明，否则 ctypes 默认按 32 位 int 截断
_user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
_user32.OpenClipboard.restype = ctypes.wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = ctypes.wintypes.BOOL
_user32.IsClipboardFormatAvailable.argtypes = [ctypes.wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = ctypes.wintypes.BOOL
_user32.GetClipboardData.argtypes = [ctypes.wintypes.UINT]
_user32.GetClipboardData.restype = ctypes.wintypes.HANDLE
_kernel32.GlobalLock.argtypes = [ctypes.wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = ctypes.wintypes.BOOL
_kernel32.GlobalSize.argtypes = [ctypes.wintypes.HGLOBAL]
_kernel32.GlobalSize.restype = ctypes.c_size_t


def _get_clipboard_text() -> str:
    """读取剪贴板文本。优先 CF_UNICODETEXT（现代应用可能只写这一种）。"""
    if not _user32.OpenClipboard(None):
        return ""
    try:
        if _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            fmt = CF_UNICODETEXT
        elif _user32.IsClipboardFormatAvailable(CF_TEXT):
            fmt = CF_TEXT
        else:
            return ""

        h_data = _user32.GetClipboardData(fmt)
        if not h_data:
            return ""
        p_data = _kernel32.GlobalLock(h_data)
        if not p_data:
            return ""
        try:
            size = _kernel32.GlobalSize(h_data)
            if size == 0 or size > 4096:
                return ""
            if fmt == CF_UNICODETEXT:
                return ctypes.wstring_at(p_data)
            # CF_TEXT 按系统 ANSI 代码页存放，不是 UTF-8
            return ctypes.string_at(p_data).decode("mbcs", errors="ignore")
        finally:
            _kernel32.GlobalUnlock(h_data)
    except Exception:
        return ""
    finally:
        _user32.CloseClipboard()


# ============================================================================
# mpv 启动
# ============================================================================

_ipc_capable = None           # 首次拉起 mpv 时探测一次并缓存
_ipc_capable_lock = threading.Lock()

_PIPE_PREFIX = "\\\\.\\pipe\\"


def _mpv_supports_ipc(mpv_path: str) -> bool:
    """这个 mpv 认不认 --input-ipc-server。必须先看一眼：mpv 对不认识的
    命令行选项是 fatal 退出，冒然加上会把播放本身一起带走。"""
    global _ipc_capable
    with _ipc_capable_lock:
        if _ipc_capable is None:
            try:
                out = subprocess.run(
                    [mpv_path, "--list-options"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW
                    if sys.platform == "win32" else 0).stdout
                _ipc_capable = "input-ipc-server" in out
            except Exception as e:
                _log(f"[!] 探测 mpv IPC 能力失败（{type(e).__name__}），"
                     "本次不传 --input-ipc-server")
                _ipc_capable = False
    return _ipc_capable


def _mpv_ipc_pipe() -> str:
    """管道名带随机段：本机其他进程猜不到，也就驱动不了这个 mpv。"""
    return f"cbtompv-{os.getpid()}-{secrets.token_hex(3)}"


def _ipc_ask(raw, rd, prop: str, rid: int, deadline: float):
    """发一条 get_property，读到与 rid 配对的回应为止。

    mpv 会把事件（start-file / audio-reconfig / file-loaded…）也推给这条连接，
    事件行没有 request_id。不跳过它们的话，播放刚开始时会把事件当回应，
    三个属性全被误判成“这个 mpv 不认”，回填就静默失效了。
    """
    raw.write(json.dumps({"command": ["get_property", prop], "request_id": rid})
              .encode("utf-8") + b"\n")
    raw.flush()
    while time.time() < deadline:
        line = rd.readline()
        if not line:
            return None                       # mpv 退出，管道关了
        try:
            obj = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if obj.get("event"):
            continue
        if obj.get("request_id") == rid:
            return obj
    return None


def _fetch_media_info(pipe: str, timeout: float = 30.0) -> dict:
    """轮询 mpv 的属性，直到拿到片名和编码、或超时。拿不到的键直接缺席。

    顺序与放弃条件都有讲究：片名要等 ytdl_hook 解析完（实测 1~5 秒，慢的
    时候更久），所以它只能等到超时；编码属性可能永久 unavailable（纯音频
    没有视频轨），问满几次就算没有，否则一条音频要白等满整个超时。
    """
    wanted = ["media-title", "audio-codec", "video-codec"]
    info, misses = {}, {}
    deadline = time.time() + timeout
    raw = None
    try:
        while raw is None:
            try:
                raw = open(_PIPE_PREFIX + pipe, "r+b", buffering=0)
            except OSError:
                if time.time() >= deadline:
                    return info       # mpv 没建管道，放弃回填
                time.sleep(0.1)       # 建管道有几十毫秒延迟
        rd = io.BufferedReader(raw)
        rid = 0
        while wanted and time.time() < deadline:
            prop = wanted[0]
            rid += 1
            r = _ipc_ask(raw, rd, prop, rid, deadline)
            if r is None:
                break
            err = r.get("error")
            if err == "success" and r.get("data"):
                info[prop] = r["data"]
                wanted.pop(0)
            elif err != "property unavailable":
                wanted.pop(0)         # 这个 mpv 不认该属性，别再问
            elif prop != "media-title":
                misses[prop] = misses.get(prop, 0) + 1
                if misses[prop] >= 5:
                    wanted.pop(0)     # 该轨不存在（如纯音频无视频编码）
                else:
                    time.sleep(0.4)
            else:
                time.sleep(0.4)       # 片名还没解析出来，继续等到超时
    except Exception as e:
        _log(f"[!] 读取 mpv 播放信息失败: {type(e).__name__}")
    finally:
        if raw is not None:
            raw.close()
    return info


def _short(text: str, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _enrich_history(pipe: str, url: str, key: str):
    """后台线程：把 mpv 解析出的片名/编码补回播放历史那一行。

    失败就什么都不做 —— 历史里那行 URL 推导的标识已经是可用的。
    """
    info = _fetch_media_info(pipe)
    title = str(info.get("media-title") or "")
    if not title or title == url:
        return
    codecs = [str(info.get(p) or "").split(" ")[0] for p in ("video-codec", "audio-codec")]
    codec = " + ".join(c for c in codecs if c)
    detail = f"{_short(title, 26)} · {_short(codec, 14)}" if codec else _short(title, 26)
    _ui_push({"key": key, "detail": detail}, "history_detail")
    _log(f"[+] 播放信息：{title}" + (f" [{codec}]" if codec else ""))


def _launch_mpv(url: str, mpv_path: str, ytdlp_path: str,
                mpv_confdir: str, cookies_file: str, browser_cookie: str,
                proxy: str):
    cmd = [mpv_path, url]

    # ── 窗口 & 硬件解码 ──
    cmd += [
        "--force-window=yes",       # 强制创建窗口（避免 DASH 流加载时黑屏无画）
        "--hwdec=auto-safe",        # 稳妥硬解（优先 D3D11VA，失败回退软解）
        "--ontop",                  # 置顶窗口，不被浏览器遮挡
    ]

    if mpv_confdir:
        cmd += [f"--config-dir={mpv_confdir}"]
    if ytdlp_path:
        cmd += [f"--script-opts=ytdl_hook-ytdl_path={ytdlp_path}"]
    if proxy:
        cmd.append(f"--http-proxy={proxy}")

    # Cookie 优先级：文件 > 浏览器 > 无
    if cookies_file:
        cmd.append(f"--ytdl-raw-options=cookies={cookies_file}")
    elif browser_cookie:
        cmd.append(f"--ytdl-raw-options=cookies-from-browser={browser_cookie}")

    # 画质：最高分辨率（2160p）+ HDR/杜比 + 最高音质
    cmd.append("--ytdl-format=bestvideo[height<=2160]+bestaudio/bestvideo+bestaudio/best")

    ipc_pipe = _mpv_ipc_pipe() if _mpv_supports_ipc(mpv_path) else ""
    if ipc_pipe:
        cmd.append(f"--input-ipc-server={ipc_pipe}")

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except FileNotFoundError:
        _log(f"[!] 找不到 mpv: {mpv_path}")
        return
    except Exception as e:
        _log(f"[!] 拉起 mpv 失败: {e}")
        return

    if ipc_pipe:
        threading.Thread(target=_enrich_history,
                         args=(ipc_pipe, url, _history_key(url)),
                         daemon=True).start()


# ============================================================================
# 链接提取
# ============================================================================

# 分享文案常带中英文标点收尾，延伸取整段 URL 后需剥掉
_TRAILING = ".,;:!?。，；：！？、)）】》>\"'"


def _extend_to_token(text: str, m: re.Match) -> str:
    """把匹配延伸到空白符为止，取回含查询串的完整 URL。"""
    end = m.end()
    while end < len(text) and text[end] not in "\r\n\t ":
        end += 1
    return text[m.start():end].rstrip(_TRAILING)


# 这些查询参数是标识而非跟踪参数：p= 分P、fid= 收藏夹、sid= 合集
_KEEP_QUERY = re.compile(r"(?:^|&)(p|fid|sid)=(\d+)")


def _clean_bili_url(url: str) -> str:
    """补 scheme、m. 域名改 www.、剥除跟踪参数但保留 p= / fid= / sid=。"""
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    url = url.replace("://m.bilibili.com/", "://www.bilibili.com/", 1)

    base, _, query = url.partition("?")
    base = base.rstrip("/")
    keep = _KEEP_QUERY.findall(query)
    if not keep:
        return base
    return base + "?" + "&".join(f"{k}={v}" for k, v in keep)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """把 3xx 当普通响应返回，只要 Location，不跟随（跟随会白下载整页 HTML）。"""

    def http_error_302(self, req, fp, code, msg, headers):
        return fp

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _resolve_b23(url: str) -> str | None:
    """b23.tv 短链 → 真实 bilibili.com 地址。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(req, timeout=5) as resp:
            location = resp.headers.get("Location")
            if location:
                return urllib.parse.urljoin(url, location)
            return resp.url if resp.code == 200 else None
    except Exception as e:
        _log(f"[!] b23.tv 短链解析失败: {e}")
        return None


def _extract_url(text: str) -> str | None:
    m = B23_RE.search(text)
    if m:
        resolved = _resolve_b23(_clean_bili_url(_extend_to_token(text, m)))
        return _clean_bili_url(resolved) if resolved else None

    for pattern in (BILI_RE, BILI_SPACE_RE, BILI_LIVE_RE):
        m = pattern.search(text)
        if m:
            return _clean_bili_url(_extend_to_token(text, m))

    m = YT_RE.search(text)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"

    # 裸 BV 号放最后：最宽松，最容易误判
    m = BV_RE.search(text)
    if m:
        return f"https://www.bilibili.com/video/{m.group(0)}"

    return None


# ============================================================================
# 单实例
# ============================================================================

_MUTEX_NAME = "Local\\CB-to-MPV"
_ERROR_ALREADY_EXISTS = 183

_MB_OK = 0x0
_MB_ICONWARNING = 0x30
_MB_SETFRONT = 0x00040000

_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL,
                                   ctypes.wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
_kernel32.GetLastError.restype = ctypes.wintypes.DWORD
_user32.MessageBoxW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPCWSTR,
                                ctypes.wintypes.LPCWSTR, ctypes.wintypes.UINT]
_user32.MessageBoxW.restype = ctypes.c_int


def _acquire_single_instance():
    """已有实例在跑则返回 None。句柄不显式关闭，随进程退出自动释放。"""
    handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return handle
    if _kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle


def _notify_already_running():
    """用原生对话框，而不是窗口或日志：此时既没有 Tk 主窗口，也不能写日志。"""
    _user32.MessageBoxW(
        None,
        "CB-to-MPV 已经在运行，剪贴板监听由先启动的那个窗口负责。\n"
        "本次启动直接退出。",
        "剪贴板直连播放器",
        _MB_OK | _MB_ICONWARNING | _MB_SETFRONT)


# ============================================================================
# 主窗口
#
# 关窗即退出：监听跑在守护线程里，主线程的 mainloop 一返回进程就结束。
# ============================================================================

_paused = threading.Event()
_stopping = threading.Event()


class _Window:
    def __init__(self):
        global _ui_on
        self.root = tk.Tk()
        self.root.title("剪贴板直连播放器 — B站 / YouTube")
        self.root.geometry("500x440")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        pad = ttk.Frame(self.root, padding=15)
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="剪贴板直连播放器 — B站 / YouTube",
                  font=("Microsoft YaHei", 12, "bold")).pack(pady=(0, 10))

        logf = ttk.LabelFrame(pad, text="状态", padding=6)
        logf.pack(fill="both", expand=True)
        self.status = self._box(logf, 12)

        histf = ttk.LabelFrame(pad, text="播放历史", padding=6)
        histf.pack(fill="both", expand=True, pady=(8, 0))
        self.hist = self._box(histf, 4)

        btns = ttk.Frame(pad)
        btns.pack(fill="x", pady=(10, 0))
        self.pause_btn = ttk.Button(btns, text="暂停监听", command=self._toggle_pause)
        self.pause_btn.pack(side="left")
        ttk.Button(btns, text="清空历史",
                   command=self._clear_hist).pack(side="left", padx=6)
        ttk.Button(btns, text="扫码登录 B 站",
                   command=self.open_login).pack(side="left")
        ttk.Button(btns, text="退出", command=self.close).pack(side="right")

        _ui_on = True
        self._login = None          # 扫码窗口，同一时刻只允许一张二维码
        self.root.after(80, self._pump)

    @staticmethod
    def _box(parent, height: int):
        t = tk.Text(parent, height=height, width=58, state="disabled",
                    font=("Consolas", 9), bg="#fafafa", relief="flat", border=0)
        t.pack(fill="both", expand=True)
        return t

    def _pump(self):
        try:
            while True:
                msg, kind = _ui_q.get_nowait()
                if kind == "login":
                    self.open_login(auto=True)
                    continue
                if kind == "history_detail":
                    self._apply_hist_detail(msg["key"], msg["detail"])
                    continue
                self._append(self.hist if kind == "history" else self.status, msg)
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    @staticmethod
    def _append(box, msg: str):
        box.configure(state="normal")
        box.insert("end", msg + "\n")
        box.see("end")
        box.configure(state="disabled")

    def _apply_hist_detail(self, key: str, detail: str):
        """把最近一条含 key 的历史行尾部换成片名 + 编码；找不到就不动。"""
        box = self.hist
        box.configure(state="normal")
        try:
            for row in range(int(box.index("end-1c").split(".")[0]), 0, -1):
                line = box.get(f"{row}.0", f"{row}.0 lineend")
                if key not in line:
                    continue
                base = re.sub(r"\s*→ mpv\s*$", "", line)
                room = 58 - len(base) - len("  ·  ")
                if room < 6:
                    break                     # 塞不下就不改，保住原来那行
                box.delete(f"{row}.0", f"{row}.0 lineend")
                box.insert(f"{row}.0", f"{base}  ·  {_short(detail, room)}")
                break
        finally:
            box.configure(state="disabled")

    def _clear_hist(self):
        box = self.hist
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.configure(state="disabled")

    def _toggle_pause(self):
        if _paused.is_set():
            _paused.clear()
            self.pause_btn.configure(text="暂停监听")
            _log("[*] 已继续监听。")
        else:
            _paused.set()
            self.pause_btn.configure(text="继续监听")
            _log("[*] 已暂停监听，复制链接不再播放。")

    # ── 扫码登录 ──

    def open_login(self, auto: bool = False):
        if self._login is not None:
            return                      # 已经开着，别叠第二张二维码
        win = tk.Toplevel(self.root)
        cancel = threading.Event()
        box: queue.Queue = queue.Queue()
        self._login, self._login_cancel, self._login_q = win, cancel, box
        path = _cookies_path()

        win.title("B 站扫码登录")
        win.geometry("340x430")
        win.resizable(False, False)
        win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", lambda: self._close_login(win, cancel))
        if auto:
            _log("[*] 已自动打开扫码窗口，登录后画质自动恢复。")

        ttk.Label(win, text="用 B 站 App「扫一扫」，在手机确认即完成登录",
                  font=("Microsoft YaHei", 9), foreground="#666").pack(pady=(16, 8))
        canvas = tk.Canvas(win, width=290, height=290, bg="white",
                           highlightthickness=0)
        canvas.pack()
        info = ttk.Label(win, text="正在获取二维码…",
                         font=("Microsoft YaHei", 10, "bold"))
        info.pack(pady=(12, 4))
        ttk.Button(win, text="取消",
                   command=lambda: self._close_login(win, cancel)).pack(pady=(6, 14))

        def worker():
            # 工作线程只往队列里投消息，绝不直接碰 Tk —— 界面更新由 _pump_login
            # 在主线程取走。跨线程调 root.after 会踩坏 Tcl 解释器。
            try:
                ok = _qr_login(path, cancel,
                               draw_qr=lambda m: box.put(("qr", m)),
                               on_status=lambda s: box.put(("status", s)))
            except Exception as e:
                # 磁盘写不进（cookies.txt 被占用/路径无效）也会走到这里，
                # 不兜住的话窗口会永远停在「正在获取二维码…」
                _log(f"[!] 扫码流程异常：{type(e).__name__}: {e}")
                ok = False
            box.put(("done", ok))

        threading.Thread(target=worker, daemon=True).start()
        self._pump_login(win, canvas, info, cancel)

    def _pump_login(self, win, canvas, info, cancel):
        if win is not self._login:
            return                          # 窗口已关，停止续期
        try:
            while True:
                kind, payload = self._login_q.get_nowait()
                if kind == "qr":
                    self._draw_qr(canvas, payload)
                elif kind == "status":
                    info.configure(text=payload)
                elif kind == "done":
                    if payload:
                        self._after_login(win, cancel)
                    else:
                        self._close_login(win, cancel)
                    return
        except queue.Empty:
            pass
        win.after(120, lambda: self._pump_login(win, canvas, info, cancel))

    @staticmethod
    def _draw_qr(canvas, matrix):
        """点阵直接画成矩形 —— 不引入 PIL，Tkinter 只要 canvas 就够。"""
        canvas.delete("all")
        n = len(matrix)
        if not n:
            return
        cell = 280 // n
        off = (290 - cell * n) // 2
        for y, row in enumerate(matrix):
            for x, on in enumerate(row):
                if on:
                    canvas.create_rectangle(off + x * cell, off + y * cell,
                                            off + (x + 1) * cell,
                                            off + (y + 1) * cell,
                                            fill="black", outline="")

    def _after_login(self, win, cancel):
        # 扫完当场验一次：服务端裁决比「文件写成功了」更能说明问题
        state, detail = _nav_verdict(_resolve_cookies())
        if state == "ok":
            _log(f"[+] 登录态已生效（{detail}），4K / 高码率按账号权限开放。")
        elif state == "network":
            _log("[*] 暂时无法联网复核，下次播放时自然生效。")
        else:
            _log(f"[!] 扫码后服务端仍不认这份 Cookie（{detail}）")
        self._close_login(win, cancel)

    def _close_login(self, win, cancel):
        if cancel is not None:
            cancel.set()            # 让还在 sleep 的轮询线程下一轮就退出
        if self._login is win or self._login is None:
            self._login = None
        try:
            win.destroy()
        except tk.TclError:
            pass

    def close(self):
        global _ui_on
        _stopping.set()
        _ui_on = False
        self.root.destroy()


# ============================================================================
# 主循环
# ============================================================================

def _history_key(url: str) -> str:
    """播放历史一行的识别段，也是 IPC 回填时定位那一行的锚点。"""
    if "youtube.com" in url or "youtu.be" in url:
        m = YT_RE.search(url)
        return f"YouTube {m.group(1) if m else url}"
    m = BV_RE.search(url)
    ident = m.group(0) if m else url.partition("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return f"B站 {ident}"


def _history_line(url: str) -> str:
    """播放历史的一行。片名和编码在 mpv 侧解析，稍后由 _enrich_history 追写。"""
    return f"[{time.strftime('%H:%M')}] {_history_key(url)}  → mpv"


def _listen(mpv_path: str, ytdlp_path: str, mpv_confdir: str,
            cookies_file: str, browser_cookie: str, proxy: str):
    # 启动时用当前剪贴板打底：否则会把之前复制过的旧链接立刻播出来，
    # 开机自启时就变成「一登录弹一个视频」。
    last_text = _get_clipboard_text()
    last_url = ""
    while not _stopping.is_set():
        try:
            text = _get_clipboard_text()
            if _paused.is_set():
                # 暂停期间仍跟随剪贴板基线，否则恢复瞬间会把暂停时复制的内容播出来
                if text:
                    last_text = text
            elif text and text != last_text:
                last_text = text
                # 仅在剪贴板内容变化时才解析：b23.tv 短链要发一次网络请求，
                # 不能每 0.5 秒重复解析同一个链接。
                url = _extract_url(text)
                if url and url != last_url:
                    last_url = url
                    # 每次都重新解析 Cookie 路径：扫码登录刚写过文件，
                    # 沿用启动时缓存的旧值会让登录后仍然按游客画质播放
                    cookies_file = _resolve_cookies()
                    _log(f">> {url}")
                    _ui_push(_history_line(url), "history")
                    # 稍后再看是私有列表，游客模式必然失败，提前说明原因
                    if "/watchlater" in url and not (cookies_file or browser_cookie):
                        _log("[!] 未配置 Cookie，稍后再看会解析失败："
                             "点「扫码登录 B 站」，或填写 cookies.txt")
                    _launch_mpv(url, mpv_path, ytdlp_path, mpv_confdir,
                                cookies_file, browser_cookie, proxy)
            _stopping.wait(0.5)
        except Exception as e:
            _log(f"[!] {e}")
            _stopping.wait(1)
    _log("[*] 已停止监听。")


def _startup():
    """后台线程：解析路径 → 打 banner → 进入监听循环。

    放后台是因为 Cookie 探测和 b23.tv 解析都要等网络，主线程必须留给窗口。
    """
    # ── 路径解析 ──
    mpv_path    = _resolve_mpv()
    ytdlp_path  = _resolve_ytdlp()
    mpv_confdir = _resolve_mpv_confdir()
    cookies_file   = _resolve_cookies()
    browser_cookie = _resolve_browser_cookie()
    proxy          = _resolve_proxy()
    autostart      = _resolve_autostart()

    _log(f"[*] CB-to-MPV 启动，PID={os.getpid()}，日志: {LOG_PATH}")

    # 开机自启
    _setup_autostart(autostart)

    if Path(mpv_path).is_file():
        _log(f"[+] mpv:      {mpv_path}")
    else:
        _log(f"[!] mpv 未找到（将依赖 PATH）: {mpv_path}")
    if ytdlp_path:
        _log(f"[+] yt-dlp:   {ytdlp_path}")
    else:
        _log("[!] 未找到 yt-dlp.exe，B 站 / YouTube 均无法解析")
    if mpv_confdir:
        _log(f"[+] config:   {mpv_confdir}")
    if proxy:
        _log(f"[+] 代理:     {proxy}")
    if cookies_file:
        _log(f"[+] Cookie:   {cookies_file}")
        _probe_cookies_file(cookies_file)
    elif browser_cookie:
        _log(f"[+] Cookie:   cookies-from-browser={browser_cookie}")
        _probe_browser_cookie(browser_cookie)
    else:
        _log("[*] Cookie:   未配置（游客模式，B 站 1080P / YouTube 普通画质）"
             "—— 要 4K / 高码率请点「扫码登录 B 站」")

    threading.Thread(target=_watch_auth, daemon=True,
                     name="auth-watch").start()
    _log("[*] 监听中… 复制 B 站 / YouTube 链接即可播放。")
    _listen(mpv_path, ytdlp_path, mpv_confdir, cookies_file, browser_cookie, proxy)


def main():
    mutex = _acquire_single_instance()
    if mutex is None:
        # 不能走 _log：日志以 "w" 打开，会截断正在运行那个实例的日志
        print("[*] 已有实例在运行，本次启动退出。")
        _notify_already_running()
        return

    window = _Window()
    threading.Thread(target=_startup, daemon=True).start()
    try:
        window.root.mainloop()
    except KeyboardInterrupt:
        _stopping.set()
        _log("[*] 已退出。")


if __name__ == "__main__":
    main()
