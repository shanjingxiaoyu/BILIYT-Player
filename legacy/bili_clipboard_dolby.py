#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bili_clipboard_dolby.py

后台剪贴板监听：复制 B 站链接或 BV 号 →
  自动解析杜比视界(Dolby Vision) / 杜比全景声(Dolby Atmos) →
  唤起本地 mpv/SMPlayer 播放。

不挑终端——UWP 客户端、Edge/Chrome 网页、微信/QQ 别人发的链接，复制即播。
"""

import sys
assert sys.version_info >= (3, 10), "需要 Python 3.10+"

import os
import sys
import re
import time
import json
import atexit
import hashlib
import subprocess
import urllib.parse
from functools import reduce
from pathlib import Path
from shutil import which

# ---- subprocess helpers: hide console window on Windows (no flashing cmd) ----
def _popen_silent(*args, **kwargs):
    """Popen with hidden console on Windows."""
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kwargs.setdefault("startupinfo", si)
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.Popen(*args, **kwargs)

def _run_silent(*args, **kwargs):
    """subprocess.run with hidden console on Windows."""
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kwargs.setdefault("startupinfo", si)
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.run(*args, **kwargs)

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

# ============================================================================
# 0. 自检 & 自动安装依赖
# ============================================================================

# 配置目录：优先使用 %APPDATA%，确保 exe 放在 Program Files 等受限目录时也有写权限
if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).resolve().parent
else:
    _exe_dir = Path(__file__).resolve().parent
_CONFIG_DIR = Path(os.environ.get("APPDATA", str(_exe_dir))) / "BiliYTPlayer"
_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
ENV_PATH = _CONFIG_DIR / ".env"

def _install(pkg, imp=None):
    if imp is None:
        imp = pkg
    try:
        __import__(imp.replace("-", "_"))
    except ImportError:
        print(f"[*] 安装缺失依赖: {pkg} …")
        _run_silent(
            [sys.executable, "-m", "pip", "install", pkg],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[+] {pkg} 安装完成。")


# PyInstaller 打包后依赖已随 exe bundle，无需（也无法）pip install
if not getattr(sys, "frozen", False):
    _install("requests")
    _install("python-dotenv", "dotenv")
    _install("pyperclip")


import requests                  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
import pyperclip                # noqa: E402


# ============================================================================
# 1. 常量 / 配置
# ============================================================================

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com"
COMMON_HEADERS = {
    "User-Agent": UA,
    "Referer": REFERER,
    "Origin": "https://www.bilibili.com",
}

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

BV_RE = re.compile(r"(BV[a-zA-Z0-9]{10})")
YT_RE = re.compile(r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})")
EP_RE = re.compile(r"/ep(\d+)")
SS_RE = re.compile(r"/ss(\d+)")
BANGUMI_MD_RE = re.compile(r"/md(\d+)")



# ============================================================================
# 2. SESSDATA 读取（首次弹框输入，后续从 .env 读取）
# ============================================================================

def load_sessdata() -> str:
    # 已配置过 → 直接读
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
        sessdata = os.getenv("SESSDATA", "").strip()
        if sessdata:
            return sessdata

    # 首次运行 → 弹输入框
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    sessdata = simpledialog.askstring(
        "B 站 SESSDATA",
        "首次使用，请粘贴 B 站 SESSDATA Cookie 值：\n\n"
        "获取方法：浏览器登录 bilibili.com → F12\n"
        "→ Application → Cookies → www.bilibili.com\n"
        "→ 复制 SESSDATA 的 Value  → 粘贴到此框",
        parent=root,
    )
    root.destroy()

    if not sessdata or not sessdata.strip():
        print("[!] 未输入 SESSDATA，已取消。", flush=True)
        sys.exit(0)

    sessdata = sessdata.strip().strip("\"'")
    ENV_PATH.write_text(
        "# B 站登录态 Cookie — 自动保存，请勿分享。\n"
        "SESSDATA=" + sessdata + "\n",
        encoding="utf-8",
    )
    print("[+] SESSDATA 已保存到 .env，下次运行不再询问。", flush=True)
    return sessdata


# ============================================================================
# 3. 播放器检测（mpv > smplayer.exe > smplayer.lnk）
# ============================================================================

def _search_registry_install_path(keyword: str) -> str | None:
    """搜 Windows 注册表卸载信息，找 mpv/SMPlayer 安装路径。"""
    try:
        import winreg
    except ImportError:
        return None
    roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
    subkeys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for root in roots:
        for sk in subkeys:
            try:
                with winreg.OpenKey(root, sk) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, name) as sub:
                                disp, _ = winreg.QueryValueEx(sub, "DisplayName")
                                if keyword.lower() in str(disp).lower():
                                    loc, _ = winreg.QueryValueEx(sub, "InstallLocation")
                                    if loc:
                                        return str(loc)
                        except OSError:
                            continue
            except OSError:
                continue
    return None


def find_player():
    """多级探测 mpv。优先级: 自带便携版 > 独立安装版 > SMPlayer 自带 > PATH > 注册表 > 广搜。
       返回 mpv.exe 绝对路径,找不到返 None。
    """
    drives = ["C:", "D:", "E:"]

    # ---- 0. 最高优先: 自带便携版 mpv (开箱即用) ----
    bundled = _exe_dir / "mpv-portable" / "mpv.exe"
    if bundled.is_file():
        return str(bundled)

    # ---- 0b. 从源码运行时 mpv-portable/ 位于仓库根目录（legacy/ 的上一级） ----
    if not getattr(sys, "frozen", False):
        repo_bundled = _exe_dir.parent / "mpv-portable" / "mpv.exe"
        if repo_bundled.is_file():
            return str(repo_bundled)

    # ---- 1. 独立安装的 mpv（最新稳定版） ----
    # winget 安装路径
    standalone_mpv_paths = [
        os.path.join(d + os.sep, "Program Files", "MPV Player", "mpv.exe")
        for d in drives
    ] + [
        os.path.join(d + os.sep, "Program Files (x86)", "MPV Player", "mpv.exe")
        for d in drives
    ] + [
        os.path.join(d + os.sep, "Program Files", "mpv", "mpv.exe")
        for d in drives
    ]
    for p in standalone_mpv_paths:
        if os.path.isfile(p):
            return p

    # PATH 中的 mpv（可能是 winget/scoop/choco 安装的）
    mpv_in_path = which("mpv")
    if mpv_in_path and os.path.isfile(mpv_in_path):
        # 确保不是 SMPlayer 目录下的 mpv.com（那是 mplayer 兼容层）
        if os.path.basename(mpv_in_path).lower() == "mpv.exe":
            return mpv_in_path

    def _mpv_near(sm_path):
        """从 smplayer.exe 路径推导它自带的 mpv.exe"""
        mpv_try = os.path.join(os.path.dirname(sm_path), "mpv", "mpv.exe")
        if os.path.isfile(mpv_try):
            return mpv_try
        return None

    # ---- 1. SMPlayer 固定路径（备用） ----
    for d in drives:
        for sub in ["Program Files", "Program Files (x86)", "MSplayer"]:
            sm = os.path.join(d + os.sep, sub, "SMPlayer", "smplayer.exe")
            if os.path.isfile(sm):
                mpv = _mpv_near(sm) or sm  # 没自带 mpv 就回退到 smplayer
                return mpv

    # ---- 2. PATH ----
    p = which("smplayer")
    if p:
        return _mpv_near(p) or p

    # ---- 3. 注册表 ----
    loc = _search_registry_install_path("smplayer")
    if loc:
        sm_try = os.path.join(loc, "smplayer.exe")
        if os.path.isfile(sm_try):
            return _mpv_near(sm_try) or sm_try

    # ---- 4. 广搜 Program Files ----
    for d in drives:
        for sub in ["Program Files", "Program Files (x86)"]:
            base = os.path.join(d + os.sep, sub)
            if not os.path.isdir(base):
                continue
            for root, dirs, _ in os.walk(base):
                depth = root.replace(base, "").count(os.sep)
                if depth > 3:
                    dirs.clear()
                    continue
                sm_try = os.path.join(root, "smplayer.exe")
                if os.path.isfile(sm_try):
                    return _mpv_near(sm_try) or sm_try

    # ---- 5. .lnk 快捷方式 ----
    for p in [
        r"C:\Users\Public\Desktop\SMPlayer.lnk",
        os.path.join(os.path.expanduser("~"), "Desktop", "SMPlayer.lnk"),
    ]:
        if os.path.isfile(p):
            return p  # .lnk 没法推导 mpv，直接返回

    return None


# ============================================================================
# 4. WBI 动态签名
# ============================================================================

def _mixin_key(orig: str) -> str:
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, "")[:32]


def get_wbi_keys(session: requests.Session):
    resp = session.get(
        "https://api.bilibili.com/x/web-interface/nav",
        headers=COMMON_HEADERS,
        timeout=5,
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"SESSDATA 过期或无效: {j.get('message', 'unknown')}")
    d = j["data"]["wbi_img"]
    img_key = d["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub_key = d["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    return img_key, sub_key


class _FastBiliSession:
    """山寨 requests.Session —— 绕过 urllib3 代理探测，裸 socket 直连 B站。
       提供 .get() 和 .cookies 接口，与 requests.Session 完全兼容。"""
    def __init__(self):
        self._cookies: dict[str, str] = {}

    def get(self, url: str, params: dict = None, headers: dict = None, timeout=None):
        """模拟 requests.Session.get()，返回有 .json() 和 .raise_for_status() 的对象。"""
        from urllib.parse import urlencode
        path = url.split(".com", 1)[1] if ".com" in url else url
        if params:
            path += "?" + urlencode(params)
        return _FastResponse(_fast_bili_api(path, self._cookies.get("SESSDATA", ""), headers or {}))

    @property
    def cookies(self):
        return self

    def set(self, key, value, domain=None):
        self._cookies[key] = value


class _FastResponse:
    """山寨 requests.Response —— 提供 .json() 和 .raise_for_status()。"""
    def __init__(self, data: dict):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass  # API 错误码由各调用方自行判断


def _fast_bili_api(path: str, sessdata: str = "", extra_headers: dict = None) -> dict:
    """裸 socket HTTPS 请求 —— 绕过 urllib3/requests 的代理探测开销。
       本机 requests 库有链路问题（24s），裸 socket 100ms。仅用于 B站 API。"""
    import socket, ssl, json as _json, time as _time

    host = "api.bilibili.com"
    port = 443
    timeout = 5
    deadline = _time.monotonic() + timeout  # 总体超时，防止慢速 trickle 累积

    # DNS + TCP
    addrs = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    ip = addrs[0][4][0]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((ip, port))

    # TLS
    ctx = ssl.create_default_context()
    tls = ctx.wrap_socket(sock, server_hostname=host)

    # HTTP GET
    extra = ""
    if extra_headers:
        for k, v in extra_headers.items():
            extra += f"{k}: {v}\r\n"
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: {UA}\r\n"
        + extra +
        "Accept: application/json\r\n"
        + (f"Cookie: SESSDATA={sessdata}\r\n" if sessdata else "") +
        "Connection: close\r\n"
        "\r\n"
    ).encode()
    tls.sendall(req)

    # 读响应（带总体超时）
    data = b""
    while True:
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            break
        tls.settimeout(remaining)
        try:
            chunk = tls.recv(8192)
            if not chunk:
                break
            data += chunk
        except Exception:
            break
    tls.close()

    # 解析 body（支持 chunked transfer-encoding）
    header_end = data.find(b"\r\n\r\n")
    if header_end == -1:
        raise RuntimeError("B站 API 响应格式异常")

    headers_text = data[:header_end].decode("utf-8", errors="replace")
    body = data[header_end + 4:]

    if not body:
        raise RuntimeError("B站 API 无响应")

    if "chunked" in headers_text.lower():
        # decode chunked encoding: <hex-size>\r\n<data>\r\n ... 0\r\n\r\n
        decoded = b""
        pos = 0
        while pos < len(body):
            line_end = body.find(b"\r\n", pos)
            if line_end == -1:
                break
            chunk_size = int(body[pos:line_end], 16)
            if chunk_size == 0:
                break
            pos = line_end + 2
            decoded += body[pos:pos + chunk_size]
            pos += chunk_size + 2  # skip \r\n after data
        body = decoded

    result = _json.loads(body)
    return result


def init_bili_session(sessdata: str) -> tuple:
    """初始化 B 站会话：单次 nav API 完成 SESSDATA 验证 + WBI 签名密钥获取。
       validate_sessdata() + get_wbi_keys() 两次 nav 调用合并为一次，省一半网络时间。"""
    # 裸 socket 直连 B站，绕过本机 requests 库的代理探测开销（24s → 0.1s）
    j = _fast_bili_api("/x/web-interface/nav", sessdata)

    if j.get("code") != 0:
        raise RuntimeError(f"SESSDATA 过期或无效 (code={j.get('code')}): {j.get('message', 'unknown')}")

    # 提取 WBI 签名密钥
    d = j["data"]["wbi_img"]
    img_key = d["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub_key = d["sub_url"].rsplit("/", 1)[-1].split(".")[0]

    # 创建快速 Session（裸 socket，绕过本机 requests 库的链路问题）
    session = _FastBiliSession()
    session.cookies.set("SESSDATA", sessdata, domain=".bilibili.com")

    return session, img_key, sub_key


def validate_sessdata(sessdata: str) -> bool:
    """检查 SESSDATA 是否有效（保留兼容，新代码建议用 init_bili_session）。"""
    try:
        s = requests.Session()
        s.cookies.set("SESSDATA", sessdata, domain=".bilibili.com")
        r = s.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=COMMON_HEADERS,
            timeout=5,
        )
        r.raise_for_status()
        return r.json().get("code") == 0
    except Exception:
        return False


def enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    mixin = _mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = str(int(time.time()))
    params = dict(sorted(params.items()))
    params = {
        k: "".join(c for c in str(v) if c not in "!'()*")
        for k, v in params.items()
    }
    qs = urllib.parse.urlencode(params)
    w_rid = hashlib.md5((qs + mixin).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


# ============================================================================
# 5. 视频信息 & playurl 解析
# ============================================================================

def resolve_episode(session: requests.Session, ep_id: int) -> tuple[str, int, str]:
    """通过 EP(番剧/电影) ID 获取 bvid, cid, title。
       返回 (bvid, cid, full_title)。
    """
    resp = session.get(
        "https://api.bilibili.com/pgc/view/web/season",
        params={"ep_id": ep_id},
        headers=COMMON_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"获取番剧信息失败: {j.get('message')}")
    result = j["result"]
    season_title = result.get("season_title", "") or result.get("title", "")
    for ep in result.get("episodes", []):
        if ep.get("id") == ep_id:
            bvid = ep["bvid"]
            cid = ep["cid"]
            ep_title = ep.get("long_title") or ep.get("share_copy", "") or f"第{ep.get('title','?')}集"
            full_title = f"{season_title} - {ep_title}" if season_title else ep_title
            return bvid, int(cid), full_title
    raise RuntimeError(f"未找到 EP {ep_id}")

def resolve_ss(session: requests.Session, ss_id: int) -> tuple[str, int, str]:
    """通过 SS(番剧合集) ID 获取第一个 EP 的 bvid, cid, title。
       返回 (bvid, cid, full_title)。"""
    resp = session.get(
        "https://api.bilibili.com/pgc/view/web/season",
        params={"season_id": ss_id},
        headers=COMMON_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"获取番剧合集失败: {j.get('message')}")
    result = j["result"]
    season_title = result.get("season_title", "") or result.get("title", "")
    episodes = result.get("episodes", [])
    if not episodes:
        raise RuntimeError("该合集下没有剧集")
    first_ep = episodes[0]
    return first_ep["bvid"], int(first_ep["cid"]), f"{season_title} - {first_ep.get('long_title') or first_ep.get('share_copy','') or '第' + str(first_ep.get('title', '?')) + '集'}"

def resolve_md(session: requests.Session, md_id: int) -> tuple[str, int, str]:
    """通过 MD(媒体详情页) ID 获取第一个 EP 的 bvid, cid, title。
       返回 (bvid, cid, full_title)。"""
    resp = session.get(
        "https://api.bilibili.com/pgc/review/user",
        params={"media_id": md_id},
        headers=COMMON_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"获取媒体信息失败: {j.get('message')}")
    result = j["result"]
    media_title = result.get("media", {}).get("title", "") or result.get("title", "")
    episodes = result.get("media", {}).get("episodes", []) or result.get("episodes", [])
    if not episodes:
        raise RuntimeError("该媒体页下没有剧集")
    first_ep = episodes[0]
    return first_ep["bvid"], int(first_ep["cid"]), f"{media_title} - {first_ep.get('long_title') or first_ep.get('share_copy','') or '第' + str(first_ep.get('title', '?')) + '集'}"

def get_cid(session: requests.Session, bvid: str):
    resp = session.get(
        "https://api.bilibili.com/x/web-interface/view",
        params={"bvid": bvid},
        headers=COMMON_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"获取视频信息失败: {j.get('message')}")
    return j["data"]["cid"], j["data"]["title"]


def get_playurl(session: requests.Session, bvid: str, cid: int,
                img_key: str, sub_key: str) -> dict:
    params = {
        "bvid": bvid, "cid": cid,
        "qn": 126, "fnver": 0, "fnval": 4048, "fourk": 1,
    }
    signed = enc_wbi(params, img_key, sub_key)
    resp = session.get(
        "https://api.bilibili.com/x/player/wbi/playurl",
        params=signed,
        headers=COMMON_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") != 0:
        raise RuntimeError(f"playurl 失败: {j.get('message')} (code={j.get('code')})")
    data = j.get("data")
    if not data:
        raise RuntimeError("playurl data 为空，该视频不支持杜比品质或需大会员")
    return data


def pick_dolby_streams(dash: dict):
    """提取视频/音频流。
       优先普通最高清流(兼容性最好),杜比视界仅作备选(易黑屏)。
       返回 (video_url, audio_url, vdesc, adesc)。
    """
    video_url = audio_url = None
    vdesc = adesc = ""

    dolby = dash.get("dolby") or {}

    # ── 第一优先: 普通最高清视频(兼容性最好,不会黑屏) ──
    vids = dash.get("video") or []
    if vids:
        best = sorted(vids, key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)))[-1]
        video_url = best.get("base_url") or (best.get("backup_url") or [None])[0]
        vdesc = f"普通视频 id={best.get('id')} codec={best.get('codecs', '?')}"

    # ── 普通最高码率音频 ──
    auds = dash.get("audio") or []
    if auds:
        best = sorted(auds, key=lambda x: x.get("bandwidth", 0))[-1]
        audio_url = best.get("base_url") or (best.get("backup_url") or [None])[0]
        adesc = f"普通音频 id={best.get('id')}"

    # ── FLAC 无损音频(如果可用,覆盖普通音频) ──
    flac = dash.get("flac") or {}
    fa = flac.get("audio")
    if fa:
        audio_url = fa.get("base_url") or (fa.get("backup_url") or [None])[0]
        adesc = "FLAC 无损"

    # ── 杜比全景声(如果可用,覆盖 FLAC) ──
    da = dolby.get("audio")
    if da:
        a = da[0]
        audio_url = a.get("base_url") or (a.get("backupUrl") or [None])[0]
        adesc = "杜比全景声(Dolby Atmos/E-AC-3)"

    # ── 杜比视界仅作为备选(易导致黑屏,不推荐自动使用) ──
    dv = dolby.get("video")
    if dv and not video_url:
        # 只有找不到普通流时才用杜比视界
        v = dv[0]
        video_url = v.get("base_url") or (v.get("backupUrl") or [None])[0]
        vdesc = "杜比视界(Dolby Vision) [兼容性警告]"

    return video_url, audio_url, vdesc, adesc


# ============================================================================
# 6. 唤起播放器
# ============================================================================

def _pause_browser_media():
    """发送 Windows 媒体暂停键(VK_MEDIA_PLAY_PAUSE),浏览器会响应暂停视频。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        VK_MEDIA_PLAY_PAUSE = 0xB3
        KEYEVENTF_EXTENDEDKEY = 0x0001
        KEYEVENTF_KEYUP = 0x0002
        ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_EXTENDEDKEY, 0)
        ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    except Exception:
        pass  # 静默失败,不影响主流程


def _probe_http_port(host: str, log=None, timeout: float = 0.3) -> str | None:
    """探测同 host 上常见 HTTP 代理端口是否能连通。
    返回 http://host:port/ 或 None。
    """
    import socket
    # 常见 HTTP 代理端口（按流行度排序）
    # Clash: 7890, 7891 | v2rayN: 10809, 10808 | 通用: 8080, 8118 | SS: 1080
    ports = [7890, 10809, 8080, 8118, 1080, 7891, 10808]
    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return f"http://{host}:{port}/"
        except (socket.timeout, ConnectionRefusedError, OSError):
            continue
    return None


def _resolve_proxy_url(emit=None) -> tuple[str | None, str | None]:
    """解析代理 URL: 优先环境变量,其次 Windows 系统代理注册表。

    返回 (mpv_proxy, ytdlp_proxy):
    - mpv_proxy: 给 mpv --http-proxy 用,必须是 http://host:port/ 格式(None = 不代理)
    - ytdlp_proxy: 给 yt-dlp --proxy 用,可以是 socks5:// 或 http://(None = 不代理)

    处理逻辑:
    - http:// 代理 → 两个都返回同一个
    - socks5:// 代理 → 探测同 host 的 HTTP 端口:mpv 用 http://,yt-dlp 用原 socks5://
    - PAC 脚本 → 无法解析,返回 (None, None) 并给提示
    """
    def _log(msg):
        if emit:
            try:
                emit(msg)
            except Exception:
                pass

    raw_proxy = None

    # 1) 进程级环境变量
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
        v = os.environ.get(k)
        if v and v.strip():
            raw_proxy = v.strip()
            _log(f"    [proxy] {k}={raw_proxy}")
            break

    # 2) Windows 系统代理 (注册表)
    if not raw_proxy and sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as key:
                enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
                if enable:
                    server, _ = winreg.QueryValueEx(key, "ProxyServer")
                    if server and str(server).strip():
                        raw_proxy = str(server).strip()
                        if "://" not in raw_proxy:
                            raw_proxy = "http://" + raw_proxy
                        _log(f"    [proxy] 系统代理={raw_proxy}")
        except Exception:
            pass

    if not raw_proxy:
        return None, None

    lower = raw_proxy.lower()

    # 3) PAC 自动配置脚本(ProxyServer 是 URL 不是 host:port)
    if ".pac" in lower and ("://" in lower):
        _log(f"    [proxy][!] 检测到 PAC 自动配置脚本,无法解析具体代理地址")
        _log(f"    [proxy][!] 请手动设置 HTTPS_PROXY 环境变量,或在系统代理里改用 host:port")
        return None, None

    # 4) http:// / https:// 代理 → mpv 和 yt-dlp 都直接用
    if lower.startswith(("http://", "https://")):
        return raw_proxy, raw_proxy

    # 5) socks5:// / socks4:// 代理 → 探测同 host 的 HTTP 端口
    if lower.startswith("socks"):
        import urllib.parse as up
        try:
            parsed = up.urlparse(raw_proxy)
            host = parsed.hostname or "127.0.0.1"
        except Exception:
            host = "127.0.0.1"

        _log(f"    [proxy] 检测到 socks 代理: {raw_proxy}")
        _log(f"    [proxy] mpv 不支持 socks,探测 {host} 的 HTTP 端口...")

        http_proxy = _probe_http_port(host, _log)
        if http_proxy:
            _log(f"    [proxy][+] 找到 HTTP 代理: {http_proxy}")
            _log(f"    [proxy]    mpv → {http_proxy} (拉 CDN 流)")
            _log(f"    [proxy]    yt-dlp → {raw_proxy} (解析 URL)")
            return http_proxy, raw_proxy
        else:
            _log(f"    [proxy][!] 未找到 HTTP 代理端口")
            _log(f"    [proxy][!] mpv 将直连(可能无法播放 4K 流)")
            _log(f"    [proxy][!] 建议在代理工具里开启 HTTP 代理端口(如 Clash 默认 7890)")
            return None, raw_proxy

    # 6) 其他格式,原样返回
    return raw_proxy, raw_proxy


def resolve_config_dir(player_path=None) -> Path:
    """定位 mpv 的便携配置目录（portable_config）。

    优先级（取第一个真实存在的目录）：
      1. 播放器同级的 portable_config        —— 发行包 / 独立安装的 mpv 最准确的答案
      2. <_exe_dir>/mpv-portable/portable_config —— 传统布局，保持向后兼容
      3. <_exe_dir>/portable_config
      4. 兜底返回传统布局表达式（即使不存在，行为与修改前一致）

    背景：从源码运行时 _exe_dir 指向 legacy/，而 mpv-portable/ 实际位于仓库根目录，
    旧实现会得到不存在的路径，导致 --config-dir 失效（HDR 映射等配置全部不生效）。
    """
    candidates = []
    if player_path:
        try:
            candidates.append(Path(player_path).resolve().parent / "portable_config")
        except OSError:
            pass
    candidates.append(_exe_dir / "mpv-portable" / "portable_config")
    candidates.append(_exe_dir / "portable_config")

    for cand in candidates:
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    return candidates[1]


def launch_player(player_path, video_url, title, audio_url=None, sessdata=None, log=None,
                  segments=None, sponsor_cfg=None):
    """唤起 mpv 播放。B 站（有 sessdata）走 CDN 直链 + cookie；YouTube（无 sessdata）走 ytdl_hook + 代理。

    log: 可选回调函数，用于把状态/错误信息传给 GUI（避免 print 在 --windowed 模式下被吞）。
    segments: 可选，SponsorBlock 片段列表（已过滤）。非空时注入跳过脚本，自动跳过广告。
    sponsor_cfg: 可选，SponsorBlock 配置字典（behavior/notify/debug/server 等）。
    """
    import tempfile

    def _emit(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass
        print(msg, flush=True)

    portable_conf = str(resolve_config_dir(player_path))
    launch_env = None  # 仅在注入 SponsorBlock 载荷时非 None

    # ── 公共参数 ──────────────────────────────────────────────────────────
    cmd = [
        player_path,
        video_url,
        f"--force-media-title={title}",
        f"--config-dir={portable_conf}",
        "--log-file=" + str(_CONFIG_DIR / "mpv.log"),
    ]

    # ── B 站：CDN 直链 + cookie + DASH 音视频分离，不需要 mpv 脚本 ────────
    if sessdata:
        cookie_fd, cookie_path = tempfile.mkstemp(
            suffix=".txt", prefix="bili_cookies_", text=True
        )
        with os.fdopen(cookie_fd, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write(".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\t" + sessdata + "\n")
        atexit.register(lambda p=cookie_path: os.unlink(p) if os.path.exists(p) else None)
        cmd += [
            "--load-scripts=no",   # B站走直链,跳过脚本加载提速
            "--cookies-file=" + cookie_path,
            "--http-header-fields=Referer: " + REFERER,
            "--user-agent=" + UA,
        ]
        if audio_url:
            cmd += [
                f"--audio-file={audio_url}",
                "--audio-demuxer=lavf",
                "--demuxer-lavf-probescore=100",
            ]

        # ── SponsorBlock：注入跳过脚本 + 彩色进度条 OSC ───────────────────
        # 注意：--load-scripts=no 只禁用 scripts/ 目录自动加载，不影响显式 --script，
        # 因此这里追加脚本不会拖慢启动，也不会与自动加载重复执行。
        # 载荷走环境变量而非 --script-opts：后者以逗号分隔，会截断 JSON。
        if segments:
            try:
                import sponsorblock as _sb

                launch_env = dict(os.environ)
                launch_env[_sb.PAYLOAD_ENV] = _sb.build_payload(
                    sponsor_cfg.get("bvid", "") if sponsor_cfg else "",
                    sponsor_cfg.get("cid", "") if sponsor_cfg else "",
                    segments,
                    sponsor_cfg or {},
                    title=title,
                )

                # 1) 跳过脚本（执行 seek）
                lua_path = _sb.ensure_lua_script(_CONFIG_DIR)
                if lua_path.exists():
                    cmd += ["--script=" + str(lua_path)]
                else:
                    _emit("    [!] SponsorBlock 跳过脚本写入失败")

                # 2) 受管 OSC（彩色进度条 + 去广告时长）
                #    必须配合 --osc=no：否则内置 OSC 与受管 OSC 会同时加载
                #    （实测两者会各自渲染，脚本名变成 osc2，视觉上重叠）。
                if sponsor_cfg and sponsor_cfg.get("managed_osc", True):
                    import mpv_osc as _osc

                    osc_path, osc_status = _osc.ensure_managed_osc(_CONFIG_DIR, log=_emit)
                    if osc_path:
                        cmd += ["--osc=no", "--script=" + str(osc_path)]
                    else:
                        # 打补丁失败 -> 保留 mpv 内置 OSC，功能降级但不影响播放
                        _emit("    [*] 彩色进度条不可用，使用 mpv 内置 OSC")

                _emit(f"    [+] 广告跳过: 已注入 {len(segments)} 个片段")
            except Exception as e:
                _emit(f"    [!] SponsorBlock 注入失败（已忽略）: {e}")

    # ── YouTube：ytdl_hook 解析 + 走系统代理 ──────────────────────────────
    else:
        # yt-dlp 与 mpv 同目录（发行包布局），回退到传统 mpv-portable/ 布局
        ytdlp_exe = str(Path(player_path).resolve().parent / "yt-dlp.exe")
        if not os.path.isfile(ytdlp_exe):
            ytdlp_exe = str(_exe_dir / "mpv-portable" / "yt-dlp.exe")
        if not os.path.isfile(ytdlp_exe):
            _emit(f"    [!] 未找到 yt-dlp.exe: {ytdlp_exe}")
            _emit(f"        请从 https://github.com/yt-dlp/yt-dlp/releases 下载 yt-dlp.exe")
            _emit(f"        放到 mpv-portable/ 目录下与 BiliYTPlayer.exe 同级")
            return
        # 解析代理: 返回 (mpv_proxy, ytdlp_proxy), 处理 socks5/PAC 等边缘情况
        mpv_proxy, ytdlp_proxy = _resolve_proxy_url(_emit)
        ytdl_opts = "socket-timeout=8"
        if ytdlp_proxy:
            ytdl_opts += f",proxy={ytdlp_proxy}"
        if mpv_proxy:
            cmd += [f"--http-proxy={mpv_proxy}"]
            _emit(f"    [→] mpv 代理: {mpv_proxy}")
        if ytdlp_proxy and ytdlp_proxy != mpv_proxy:
            _emit(f"    [→] yt-dlp 代理: {ytdlp_proxy}")
        cmd += [
            f"--script-opts=ytdl_hook-ytdl_path={ytdlp_exe}",
            "--ytdl-format=bestvideo[height<=2160]+bestaudio/best",
            f"--ytdl-raw-options={ytdl_opts}",
        ]

    _emit(f"    唤起 mpv: {title}")
    # 先发媒体暂停键让浏览器视频暂停,再启动 mpv(避免 mpv 也收到暂停)
    _pause_browser_media()
    time.sleep(0.2)
    # 启动 mpv: 只需抑制控制台弹窗(CREATE_NO_WINDOW), 不能用 SW_HIDE(会隐藏 mpv 主窗口!)
    popen_kw = {}
    if sys.platform == "win32":
        popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    if launch_env is not None:
        # SponsorBlock 载荷经环境变量传给 mpv 的 Lua 脚本
        popen_kw["env"] = launch_env
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, **popen_kw)

    # cookie 文件由 atexit 自动清理，无需阻塞等待

    # 快速检查 mpv 是否秒退
    time.sleep(0.3)
    code = proc.poll()
    if code is not None and code != 0:
        err = proc.stderr.read()[:500] if proc.stderr else ""
        _emit(f"    [!] mpv 异常退出 (code={code})。")
        if err:
            _emit(f"    [!] {err.strip()}")
    elif code is None:
        _emit(f"    [+] mpv 正在播放: {title}")


# ============================================================================
# 7. 剪贴板读取 + 全链路
# ============================================================================

def read_clipboard() -> str:
    """读取 Windows 剪贴板文本。
       优先用 PowerShell 原生 API（最可靠），失败则回退 pyperclip。
    """
    # 方式 1: PowerShell Get-Clipboard（用户终端会话下最稳）
    try:
        r = _run_silent(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except Exception:
        pass

    # 方式 2: pyperclip（备选）
    try:
        return pyperclip.paste()
    except Exception:
        pass

    return ""

def process_bvid(session, bvid, img_key, sub_key, player_path, sessdata):
    """B 站：CDN 直链 + SMPlayer 透传 mpv 参数。"""
    print(f"  [*] 开始解析 {bvid} …")
    cid, title = get_cid(session, bvid)
    print(f"  [+] 标题: {title}")

    data = get_playurl(session, bvid, cid, img_key, sub_key)
    dash = data.get("dash")
    if not dash:
        print("  [!] 未返回 DASH 数据，跳过。")
        return

    video_url, audio_url, vdesc, adesc = pick_dolby_streams(dash)
    if not video_url:
        print("  [!] 未能提取可播放的视频流。")
        return

    print(f"  [+] 画质: {vdesc}")
    print(f"  [+] 音轨: {adesc if audio_url else '无'}")

    launch_player(player_path, video_url, title, audio_url=audio_url, sessdata=sessdata)


# ============================================================================
# 主循环
# ============================================================================

def main():
    # 无缓冲输出，确保终端实时看到日志
    # --windowed 模式下 stdout 可能是 devnull 文件，无 reconfigure 方法，忽略即可
    for _fh in (sys.stdout, sys.stderr):
        try:
            _fh.reconfigure(line_buffering=True)
        except (AttributeError, OSError):
            pass

    print("=" * 56, flush=True)
    print(" B 站 / YouTube 剪贴板直连播放器", flush=True)
    print("=" * 56, flush=True)

    sessdata = load_sessdata()

    player_path = find_player()
    if not player_path:
        print("[!] 未检测到 SMPlayer/mpv，请先安装 SMPlayer。", flush=True)
        sys.exit(1)
    print(f"[+] 播放器: {player_path}", flush=True)

    # 单次 nav API 完成鉴权 + WBI 密钥（原来 validate + get_wbi_keys 两次调用合并）
    try:
        session, img_key, sub_key = init_bili_session(sessdata)
        print("[+] B 站鉴权 + WBI 签名密钥就绪。", flush=True)
    except Exception:
        print("[!] SESSDATA 已过期，请重新输入。", flush=True)
        ENV_PATH.unlink(missing_ok=True)
        sessdata = load_sessdata()
        try:
            session, img_key, sub_key = init_bili_session(sessdata)
            print("[+] B 站鉴权 + WBI 签名密钥就绪。", flush=True)
        except Exception as e:
            print(f"[!] B 站鉴权失败: {e}", flush=True)
            sys.exit(1)
    print("\n[*] 开始监听剪贴板（Ctrl+C 停止）…", flush=True)
    print("[*] 复制 B 站 / YouTube 链接即可播放。\n", flush=True)

    last_vid: str = ""
    heartbeat = 0

    while True:
        text = read_clipboard()

        heartbeat += 1
        if heartbeat >= 6:
            heartbeat = 0
            print(".", end="", flush=True)

        # ---- B 站 ----
        m = BV_RE.search(text)
        if m:
            bvid = m.group(1)
            if bvid != last_vid:
                last_vid = bvid
                print(f"\n>> 检测到 B 站链接: {bvid}", flush=True)
                try:
                    process_bvid(session, bvid, img_key, sub_key,
                                 player_path, sessdata)
                except Exception as e:
                    print(f"  [!] 播放失败: {e}", flush=True)
            time.sleep(0.5)
            continue

        # ---- YouTube：有代理走代理，没代理直连（国外正常/国内连不上） ----
        m = YT_RE.search(text)
        if m:
            ytid = m.group(1)
            if ytid != last_vid:
                last_vid = ytid
                print(f"\n>> 检测到 YouTube 链接: {ytid}", flush=True)
                url = f"https://www.youtube.com/watch?v={ytid}"
                launch_player(player_path, url, url)
            time.sleep(0.5)
            continue

        # ---- 番剧/电影 (EP/SS/MD) ----
        m = EP_RE.search(text)
        if m:
            ep_id = m.group(1)
            vid_key = f"ep{ep_id}"
            if vid_key != last_vid:
                last_vid = vid_key
                print(f"\n>> 检测到番剧/电影 EP: {ep_id}", flush=True)
                try:
                    bvid, cid, full_title = resolve_episode(session, int(ep_id))
                    print(f"  [+] {full_title} (BV={bvid})", flush=True)
                    data = get_playurl(session, bvid, cid, img_key, sub_key)
                    dash = data.get("dash")
                    if not dash:
                        print("  [!] 未返回 DASH 数据，跳过。", flush=True)
                    else:
                        video_url, audio_url, vdesc, adesc = pick_dolby_streams(dash)
                        if video_url:
                            print(f"  [+] 画质: {vdesc}", flush=True)
                            print(f"  [+] 音轨: {adesc if audio_url else '无'}", flush=True)
                            launch_player(player_path, video_url, full_title, audio_url=audio_url, sessdata=sessdata)
                        else:
                            print("  [!] 未能提取可播放的视频流。", flush=True)
                except Exception as e:
                    print(f"  [!] 播放失败: {e}", flush=True)
            time.sleep(0.5)
            continue

        # ---- 番剧/剧集 (SS) ----
        m = SS_RE.search(text)
        if m:
            ss_id = m.group(1)
            vid_key = f"ss{ss_id}"
            if vid_key != last_vid:
                last_vid = vid_key
                print(f"\n>> 检测到番剧 SS: {ss_id}", flush=True)
                try:
                    bvid, cid, full_title = resolve_ss(session, int(ss_id))
                    print(f"  [+] {full_title} (BV={bvid})", flush=True)
                    data = get_playurl(session, bvid, cid, img_key, sub_key)
                    dash = data.get("dash")
                    if not dash:
                        print("  [!] 未返回 DASH 数据，跳过。", flush=True)
                    else:
                        video_url, audio_url, vdesc, adesc = pick_dolby_streams(dash)
                        if video_url:
                            print(f"  [+] 画质: {vdesc}", flush=True)
                            print(f"  [+] 音轨: {adesc if audio_url else '无'}", flush=True)
                            launch_player(player_path, video_url, full_title, audio_url=audio_url, sessdata=sessdata)
                        else:
                            print("  [!] 未能提取可播放的视频流。", flush=True)
                except Exception as e:
                    print(f"  [!] 播放失败: {e}", flush=True)
            time.sleep(0.5)
            continue

        time.sleep(0.5)


if __name__ == "__main__":
    main()
