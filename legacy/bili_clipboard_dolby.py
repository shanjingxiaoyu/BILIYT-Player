#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bili_clipboard_dolby.py — BiliYTPlayer 后端核心库（由 bili_yt_player.pyw 调用）

职责：B 站 API 鉴权（扫码登录 / WBI 签名）→ DASH 流提取与优选 →
  构造参数唤起 mpv 播放；经 MPV 命名管道 IPC 实现切集与自动连播。

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
import socket
import hashlib
import threading
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
    _install("qrcode")


import requests  # noqa: E402


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

# 视频编码权重：仅作同分辨率、码率接近时的决胜手（AV1>HEVC>AVC）。
# 注意：不再是绝对优先。2026-10-22 实测 B 站 4K 档给 AVC 的码率是 AV1 的
# 2.2~3.3 倍（9273/3333/2784kbps 等三组数据），AV1 的效率优势补不回 3 倍
# 码率差，码率优先才是画质最高。详见 git log。
CODEC_WEIGHT = {13: 3, 12: 2, 7: 1}



# ============================================================================
# 2. SESSDATA 读取（QR 扫码登录，替代手工凭证）
# ============================================================================

def qr_login_sessdata(emit=None, draw_qr=None, on_status=None,
                      cancel=None, timeout: float = 180) -> str | None:
    """B 站官方扫码登录。

    流程:
      generate 申请二维码 → 终端 ASCII 二维码（可同时交给 GUI 绘制）→
      轮询 poll 直到授权成功 → 从 Set-Cookie 提取 SESSDATA → 保存到 .env。

    参数:
      emit:      日志回调（GUI 传入 self._log，终端走 print）
      draw_qr:   二维码矩阵回调 (matrix)，GUI 用它绘制 Canvas
      on_status: 状态文案回调（"请扫码"/"已扫码待确认"/"成功"）
      cancel:    threading.Event，置位则中止轮询（GUI 取消按钮）
      timeout:   轮询超时秒数

    返回 SESSDATA 或 None（失败/取消/超时）。
    """
    import qrcode as _qrcode  # noqa: E402 — 懒加载，纯终端场景也可用

    def _emit(msg):
        if emit:
            try:
                emit(msg)
            except Exception:
                pass
        print(msg, flush=True)

    # 1) 申请二维码
    _emit("[*] 正在申请 B 站登录二维码…")
    try:
        gen_header, gen_body = _fast_https_request(
            "passport.bilibili.com",
            "/x/passport-login/web/qrcode/generate",
            timeout=10,
        )
    except Exception as e:
        _emit(f"[!] 二维码申请失败: {e}")
        return None

    try:
        gen = json.loads(gen_body)
    except Exception:
        _emit("[!] 二维码接口返回异常")
        return None

    if gen.get("code") != 0:
        _emit(f"[!] 二维码申请失败: {gen.get('message')}")
        return None

    qr_url = gen["data"]["url"]
    qr_key = gen["data"]["qrcode_key"]

    # 提取 generate 的 Set-Cookie（buvid3 等，poll 需要携带）
    cookie_str = ""
    for m in re.finditer(r"^Set-Cookie:\s*([^;]+)=([^;]+)", gen_header, re.I | re.M):
        if m.group(1).strip().lower() == "buvid3":
            cookie_str = f"{m.group(1)}={m.group(2)}"
            break

    # 2) 展示二维码（终端 ASCII + 可选 GUI 绘制）
    qr = _qrcode.QRCode(border=1)
    qr.add_data(qr_url)
    qr.make(fit=True)

    _emit("[*] 请用 B 站 App 扫码登录：")
    try:
        qr.print_ascii(invert=True)  # 终端 ASCII 二维码
    except Exception:
        pass
    if draw_qr:
        try:
            draw_qr(qr.modules)
        except Exception:
            pass
    if on_status:
        try:
            on_status("请用 B 站 App 扫码登录")
        except Exception:
            pass

    # 3) 轮询 poll 直到授权成功
    poll_path = "/x/passport-login/web/qrcode/poll?qrcode_key=" + qr_key
    deadline = time.time() + timeout

    while time.time() < deadline:
        if cancel is not None and cancel.is_set():
            _emit("[!] 已取消扫码登录。")
            return None

        try:
            poll_header, poll_body = _fast_https_request(
                "passport.bilibili.com", poll_path,
                cookie=cookie_str, timeout=10,
            )
            pj = json.loads(poll_body)
        except Exception as e:
            _emit(f"[!] poll 请求失败: {e}，重试…")
            time.sleep(2)
            continue

        data = pj.get("data") or {}
        code = data.get("code", -1)

        if code == 0:  # 登录成功
            # 从 Set-Cookie 提取 SESSDATA
            sessdata = ""
            for m in re.finditer(r"^Set-Cookie:\s*SESSDATA=([^;]+)", poll_header, re.I | re.M):
                sessdata = m.group(1)
                break
            if not sessdata:
                # 兜底：部分网关把 SESSDATA 放在 data.url 的 query 里
                m = re.search(r"[?&]SESSDATA=([^;&]+)", data.get("url", ""))
                if m:
                    sessdata = m.group(1)

            if not sessdata:
                _emit("[!] 登录成功但未解析到 SESSDATA，请重试。")
                return None

            ENV_PATH.write_text(
                "# B 站登录态 Cookie — 扫码登录自动保存，请勿分享。\n"
                "SESSDATA=" + sessdata + "\n",
                encoding="utf-8",
            )
            _emit("[+] 扫码登录成功，SESSDATA 已保存到 .env。")
            if on_status:
                try:
                    on_status("登录成功！")
                except Exception:
                    pass
            return sessdata

        elif code == 86038:  # 二维码失效
            _emit("[!] 二维码已失效，请重新运行程序再扫一次。")
            return None
        elif code == 86090:  # 已扫码，待确认
            if on_status:
                try:
                    on_status("已扫码，请在手机上确认…")
                except Exception:
                    pass
            _emit("[*] 已扫码，请在手机上点击确认…")
        elif code == 86101:  # 未扫码
            pass  # 继续轮询，不刷屏

        time.sleep(2)

    _emit("[!] 扫码超时。")
    return None


# ============================================================================
# 3. 播放器检测（只找 mpv.exe：便携版 > 独立安装版 > PATH）
# ============================================================================

def find_player():
    """多级探测 mpv。优先级: 自带便携版 > 独立安装版 > PATH。
       返回 mpv.exe 绝对路径,找不到返 None。
    """
    drives = ["C:", "D:", "E:"]

    # ---- 0. 最高优先: 自带便携版 mpv (开箱即用) ----
    bundled = _exe_dir / "mpv-portable" / "mpv.exe"
    if bundled.is_file():
        return str(bundled)

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
        if os.path.basename(mpv_in_path).lower() == "mpv.exe":
            return mpv_in_path

    return None


# ============================================================================
# 4. WBI 动态签名
# ============================================================================

def _mixin_key(orig: str) -> str:
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, "")[:32]


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
        # timeout 必须透传：调用方（playurl=15s 等）的预算曾经被静默丢弃
        return _FastResponse(_fast_bili_api(
            path, self._cookies.get("SESSDATA", ""), headers or {},
            timeout=timeout if timeout else 10,
        ))

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


def _fast_https_request(host: str, path: str, cookie: str = "",
                        extra_headers: dict = None, timeout: float = 10,
                        retries: int = 1) -> tuple[str, bytes]:
    """裸 socket HTTPS GET —— 绕过 urllib3/requests 的代理探测开销。
       支持任意 host（api.bilibili.com / passport.bilibili.com 等）。
       返回 (header_text, body_bytes)：header_text 完整保留，调用方可用
       正则提取 Set-Cookie（扫码登录需要）等头字段。
       本机 requests 库有链路问题（24s），裸 socket 100ms。

       超时语义（2026-10-19 修）：连接/读取 stall 一律抛 TimeoutError，
       不再静默返回截断 body（截断会让 json.loads 报难懂的错）。
       瞬时 stall 自动重试 retries 次；连接失败时轮换 getaddrinfo 地址。
    """
    import socket, ssl, time as _time

    last_err = None
    for attempt in range(retries + 1):
        try:
            return _fast_https_once(host, path, cookie, extra_headers, timeout)
        except (socket.timeout, TimeoutError) as e:
            last_err = e
            continue
    raise TimeoutError(f"B站 API 请求超时（{timeout}s × {retries + 1} 次）: {host}")


def _fast_https_once(host: str, path: str, cookie: str = "",
                     extra_headers: dict = None, timeout: float = 10) -> tuple[str, bytes]:
    import socket, ssl, time as _time

    port = 443
    deadline = _time.monotonic() + timeout  # 总体超时，防止慢速 trickle 累积

    # DNS + TCP（连接失败/超时时轮换地址，避开偶发的坏 IP）
    addrs = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    sock = None
    last_connect_err = None
    for addr in addrs[:3]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(addr[4])
            break
        except (socket.timeout, TimeoutError):
            last_connect_err = TimeoutError(f"连接 {host} 超时")
            sock.close()
            sock = None
        except OSError as e:
            last_connect_err = e
            sock.close()
            sock = None
    if sock is None:
        raise last_connect_err or OSError(f"无法连接 {host}")

    try:
        # TLS
        tls = None
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
            + (f"Cookie: {cookie}\r\n" if cookie else "") +
            "Connection: close\r\n"
            "\r\n"
        ).encode()
        tls.sendall(req)

        # 读响应（带总体超时）
        data = b""
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"读取 {host} 响应超时")
            tls.settimeout(remaining)
            chunk = tls.recv(8192)
            if not chunk:
                break
            data += chunk
    finally:
        try:
            tls.close()
        except Exception:
            pass

    # 解析 body（支持 chunked transfer-encoding）
    header_end = data.find(b"\r\n\r\n")
    if header_end == -1:
        raise RuntimeError("HTTP 响应格式异常")

    header_text = data[:header_end].decode("utf-8", errors="replace")
    body = data[header_end + 4:]

    if not body:
        raise RuntimeError("HTTP 响应无响应体")

    if "chunked" in header_text.lower():
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

    return header_text, body


def _fast_bili_api(path: str, sessdata: str = "", extra_headers: dict = None,
                   timeout: float = 10) -> dict:
    """裸 socket HTTPS 请求 —— 绕过 urllib3/requests 的代理探测开销。
       本机 requests 库有链路问题（24s），裸 socket 100ms。仅用于 B站 API。
       保留原签名/原返回类型（dict），所有调用方无需改动。
    """
    header_text, body = _fast_https_request(
        "api.bilibili.com", path,
        cookie=f"SESSDATA={sessdata}" if sessdata else "",
        extra_headers=extra_headers,
        timeout=timeout,
    )
    return json.loads(body)


class SessdataInvalid(RuntimeError):
    """SESSDATA 缺失/过期/无效（nav API 返回非 0）。仅此异常触发扫码重登，
    网络类异常不得删除 .env——否则一次抖动就弄丢有效登录态。"""


def init_bili_session(sessdata: str) -> tuple:
    """初始化 B 站会话：单次 nav API 同时完成 SESSDATA 验证 + WBI 签名密钥获取。"""
    # 裸 socket 直连 B站，绕过本机 requests 库的代理探测开销（24s → 0.1s）
    j = _fast_bili_api("/x/web-interface/nav", sessdata)

    if j.get("code") != 0:
        raise SessdataInvalid(
            f"SESSDATA 过期或无效 (code={j.get('code')}): {j.get('message', 'unknown')}"
        )

    # 提取 WBI 签名密钥
    d = j["data"]["wbi_img"]
    img_key = d["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub_key = d["sub_url"].rsplit("/", 1)[-1].split(".")[0]

    # 创建快速 Session（裸 socket，绕过本机 requests 库的链路问题）
    session = _FastBiliSession()
    session.cookies.set("SESSDATA", sessdata, domain=".bilibili.com")

    return session, img_key, sub_key


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

def get_cid_and_title(session: requests.Session, bvid: str, p: int = 1) -> tuple[int, str]:
    """获取分 P 视频的真实 cid 与组合标题。

    请求 /x/web-interface/view 后遍历 data.pages 匹配 page == p，
    返回对应分 P 的 cid 与标题：
      - 单 P 视频:        (cid, 原标题)
      - 多 P 且 p 命中:    (cid, 原标题 - P{p} {part})
      - 多 P 且 p 越界:    回退到第 1 P，标题标 P1
    """
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
    data = j["data"]
    base_title = data.get("title", bvid)
    pages = data.get("pages") or []

    if not pages:
        return int(data["cid"]), base_title

    # 遍历 pages 匹配目标分 P
    target = None
    for pg in pages:
        if pg.get("page") == p:
            target = pg
            break
    if target is None:
        target = pages[0]  # 越界回退到第 1 P
        p = target.get("page", 1)

    cid = int(target["cid"])
    part = (target.get("part") or "").strip()

    if len(pages) > 1:
        title = f"{base_title} - P{p} {part}".rstrip() if part else f"{base_title} - P{p}"
    else:
        title = base_title

    return cid, title


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


def _stream_url(item) -> str | None:
    """从 DASH 流对象提取播放 URL，兼容驼峰(baseUrl/backupUrl)与下划线(base_url/backup_url)。
    B 站不同接口/不同时段返回的字段命名不一致，双写兼容避免音轨丢失。"""
    if not item:
        return None
    url = item.get("baseUrl") or item.get("base_url")
    if url:
        return url
    backups = item.get("backupUrl") or item.get("backup_url") or []
    return backups[0] if backups else None


def pick_dolby_streams(dash: dict):
    """提取视频/音频流 —— 画质为先，AV1 绝对优先。

    视频流: 按 (分辨率 id, bandwidth 码率, CODEC_WEIGHT 编码权重) 联合降序取首条。
            codecid 13=AV1 极清 > 12=HEVC/H.265 > 7=AVC/H.264。
            杜比视界仅作备选（易黑屏，无普通流时才用）。
    音频流: 杜比全景声 > FLAC 无损 > 最高码率普通音频。
    返回 (video_url, audio_url, vdesc, adesc)。
    """
    video_url = audio_url = None
    vdesc = adesc = ""

    dolby = dash.get("dolby") or {}

    # ── 视频流：联合降序 (id, bandwidth, codec_weight) —— 码率优先 ──
    vids = dash.get("video") or []
    if vids:
        best = sorted(
            vids,
            key=lambda x: (
                x.get("id", 0),
                x.get("bandwidth", 0),
                CODEC_WEIGHT.get(x.get("codecid"), 0),
            ),
            reverse=True,
        )[0]
        video_url = _stream_url(best)
        codecid = best.get("codecid")
        codec_label = {13: "AV1 极清", 12: "HEVC/H.265", 7: "AVC/H.264"}.get(
            codecid, f"codec{codecid}"
        )
        vdesc = f"{codec_label} id={best.get('id')} {best.get('codecs', '?')}"

    # ── 杜比视界视频仅作备选（易导致黑屏，不推荐自动使用）──
    if not video_url:
        dv = dolby.get("video")
        if dv:
            video_url = _stream_url(dv[0])
            vdesc = "杜比视界(Dolby Vision) [兼容性警告]"

    # ── 音频流：杜比全景声 > FLAC 无损 > 最高码率 ──
    da = dolby.get("audio")
    if da:
        audio_url = _stream_url(da[0])
        adesc = "杜比全景声(Dolby Atmos/E-AC-3)"
    else:
        flac = dash.get("flac") or {}
        fa = flac.get("audio")
        if fa:
            audio_url = _stream_url(fa)
            adesc = "FLAC 无损"
        else:
            auds = dash.get("audio") or []
            if auds:
                best = sorted(auds, key=lambda x: x.get("bandwidth", 0), reverse=True)[0]
                audio_url = _stream_url(best)
                adesc = f"普通音频 id={best.get('id')} {best.get('codecs', '?')}"

    # 起播/切集前的调试打印：确认音频流 URL 是否提取成功
    if not audio_url:
        print("[Debug] 警告: 未提取到音频流 URL（可能是音频字段缺失/不支持杜比）", flush=True)
    else:
        print(f"[Debug] 提取到的音频流 URL: {audio_url[:120]}", flush=True)

    return video_url, audio_url, vdesc, adesc


# ============================================================================
# 5.5 剧集列表 & MPV IPC 切集（Ctrl+←/→ 前后切集 + 播完自动下一集）
# ============================================================================

# MPV IPC 命名管道（--input-ipc-server=\\.\pipe\bili-mpv-ipc）
# 注意：mpv 的 input-ipc-server 类型标注为 [file]（Windows 用命名管道），
#       不支持 tcp:// 前缀。Python 端用 ctypes 调 kernel32 直连，零额外依赖。
IPC_PIPE = r"\\.\pipe\bili-mpv-ipc"

# 会话级剧集状态：供 IPC 后台线程切集使用
_EPISODE_STATE = {
    "episodes": [],   # [{bvid, cid, title}, ...] 按播放顺序
    "index": -1,      # 当前播放索引
    "ctx": None,      # dict(session, img_key, sub_key, sessdata)
}
_IPC_THREAD = None   # 后台 IPC 监听线程（end-file 事件）
_SIGNAL_THREAD = None  # 后台信号文件轮询线程（Lua 切集键）


# ---- Windows 命名管道客户端（ctypes，零额外依赖）----
def _pipe_open(timeout_ms: int = 3000):
    """打开 MPV 命名管道。成功返回句柄，失败返回 None。"""
    import ctypes
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3

    k32 = ctypes.windll.kernel32
    k32.WaitNamedPipeW.restype = wintypes.BOOL
    k32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    if not k32.WaitNamedPipeW(IPC_PIPE, timeout_ms):
        return None

    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    # 注意：restype=c_void_p 时 ctypes 把返回值自动转成 Python int
    # （无效句柄为 2**64-1 = 无符号 -1），不能再用 .value
    h = k32.CreateFileW(IPC_PIPE, GENERIC_READ | GENERIC_WRITE, 0, None,
                        OPEN_EXISTING, 0, None)
    if not h or h == 0xFFFFFFFFFFFFFFFF or h == 0xFFFFFFFF:
        return None

    # 切换到消息读取模式，保证一次 ReadFile 拿一条完整 JSON 消息
    k32.SetNamedPipeHandleState.restype = wintypes.BOOL
    k32.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.DWORD),
                                            ctypes.c_void_p, ctypes.c_void_p]
    mode = wintypes.DWORD(0x2)  # PIPE_READMODE_MESSAGE
    k32.SetNamedPipeHandleState(h, ctypes.byref(mode), None, None)
    return h


def _pipe_write(h, data: bytes) -> bool:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.WriteFile.restype = wintypes.BOOL
    k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    written = wintypes.DWORD()
    ok = k32.WriteFile(h, data, len(data), ctypes.byref(written), None)
    return bool(ok)


def _pipe_read_message(h, buf_size: int = 65536):
    """阻塞读一条消息。管道断开返回 b""，读取错误返回 None。"""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.ReadFile.restype = wintypes.BOOL
    k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    buf = ctypes.create_string_buffer(buf_size)
    read = wintypes.DWORD()
    ok = k32.ReadFile(h, buf, buf_size, ctypes.byref(read), None)
    if not ok:
        return None
    return buf.raw[:read.value]


def _pipe_close(h):
    import ctypes
    from ctypes import wintypes
    ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ctypes.windll.kernel32.CloseHandle(h)


def fetch_episode_list(session, source_type: str, source_id) -> list[dict]:
    """获取剧集列表（按播放顺序）。

    source_type: 'bili'（BV 分 P）| 'ep'（番剧单集）| 'ss'（番剧合集）
    返回 [{bvid, cid, title}, ...]
    """
    eps: list[dict] = []

    if source_type == "bili":
        resp = session.get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": source_id}, headers=COMMON_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        j = resp.json()
        if j.get("code") != 0:
            raise RuntimeError(f"获取视频信息失败: {j.get('message')}")
        data = j["data"]
        base = data.get("title", str(source_id))
        pages = data.get("pages") or []
        for pg in pages:
            part = (pg.get("part") or "").strip()
            if len(pages) > 1:
                t = f"{base} - P{pg.get('page')} {part}".rstrip()
            else:
                t = base
            eps.append({"bvid": str(source_id), "cid": int(pg["cid"]), "title": t})

    elif source_type in ("ep", "ss"):
        params = {"ep_id": source_id} if source_type == "ep" else {"season_id": source_id}
        resp = session.get(
            "https://api.bilibili.com/pgc/view/web/season",
            params=params, headers=COMMON_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        j = resp.json()
        if j.get("code") != 0:
            raise RuntimeError(f"获取番剧信息失败: {j.get('message')}")
        result = j["result"]
        season_title = result.get("season_title") or result.get("title") or ""
        for ep in result.get("episodes", []):
            ep_t = (ep.get("long_title") or ep.get("share_copy")
                    or f"第{ep.get('title', '?')}集")
            eps.append({
                "bvid": ep["bvid"], "cid": int(ep["cid"]),
                "title": f"{season_title} - {ep_t}" if season_title else ep_t,
            })

    return eps


def set_episode_state(episodes: list, index: int, ctx: dict):
    """记录当前剧集列表与播放上下文（IPC 切集线程消费）。"""
    _EPISODE_STATE["episodes"] = episodes or []
    _EPISODE_STATE["index"] = index if 0 <= index < len(episodes or []) else 0
    _EPISODE_STATE["ctx"] = ctx


# ---- MPV IPC 客户端（Windows 命名管道 JSON-RPC，ctypes 零依赖）----

def _ipc_send_cmd(cmd) -> bool:
    """临时连接 MPV 命名管道发送一条命令。成功返回 True。"""
    try:
        h = _pipe_open(timeout_ms=2000)
        if not h:
            return False
        try:
            payload = (json.dumps({"command": cmd}) + "\n").encode()
            return _pipe_write(h, payload)
        finally:
            _pipe_close(h)
    except Exception:
        return False


def _ipc_loadfile(video_url: str, audio_url: str | None, title: str) -> bool:
    """通过 IPC 无缝切流并挂载音频轨。

    实测结论（mpv 0.41 / win32 IPC）：
      - set_property("audio-file")  → 无效（audio-file 是 CLI option 非可写 property）
      - loadfile 的 options 参数    → 本构建整体不生效（连 start=1 都被忽略）
      - audio-add <url> auto       → ✅ 唯一运行时挂音频轨的有效途径
    切流后标题用 set_property force-media-title（可写 property）。
    """
    safe_title = (title or "").replace(",", "，")  # 逗号仅作显示安全
    _ipc_send_cmd(["set_property", "force-media-title", safe_title])
    _ipc_send_cmd(["loadfile", video_url, "replace"])
    if audio_url:
        # 实测：loadfile 未完成时 audio-add 会被丢弃，必须等文件加载完成再挂音轨；
        # select flag 强制选中该音轨（auto 不会 selected，可能导致哑剧）
        time.sleep(1.0)
        _ipc_send_cmd(["audio-add", audio_url, "select"])
    # 切集后自动续播依赖 mpv.conf 的 keep-open-pause=no（EOF 只冻帧不暂停，
    # 暂停态不会被 loadfile 的新文件继承）。不要在这里补 set pause no——
    # 那会连用户手动暂停也一并恢复，抢用户的控制权。
    return True


def get_playurl_pgc(session: requests.Session, bvid: str, cid: int) -> dict:
    """番剧(PGC)播放地址。

    2026-10-19 实测：视频 API（/x/player/wbi/playurl）对番剧一律返回
    code=-404「啥都木有」，必须走 PGC 专用 API。免费账号通常只拿到渐进式
    durl（单文件音视频混合，常见 480p）；大会员才有 DASH 分离流。
    """
    params = {
        "bvid": bvid, "cid": cid,
        "qn": 126, "fnver": 0, "fnval": 4048, "fourk": 1,
    }
    resp = session.get(
        "https://api.bilibili.com/pgc/player/web/playurl",
        params=params,
        headers=COMMON_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("code") != 0:
        raise RuntimeError(
            f"番剧 playurl 失败: {j.get('message')} (code={j.get('code')})")
    result = j.get("result") or {}
    if not (result.get("dash") or result.get("durl")):
        msg = result.get("message") or "该番剧需要大会员或所在地区不支持"
        raise RuntimeError(f"番剧无可用流: {msg}")
    return result


def pick_streams_from_result(result: dict):
    """从 playurl 结果提取流——兼容 DASH(result.dash) 与渐进式(result.durl)。

    返回 (video_url, audio_url|None, vdesc, adesc)。
    durl 是单文件混合流，audio_url 为 None（mpv 直接播，无需挂外部音轨）。
    """
    if result.get("dash"):
        return pick_dolby_streams(result["dash"])
    for d in result.get("durl") or []:
        url = d.get("url") or (d.get("backup_url") or [None])[0]
        if url:
            qmap = {126: "4K", 120: "4K", 112: "1080P+", 80: "1080P",
                    64: "720P", 32: "480P", 16: "360P"}
            q = result.get("quality")
            return url, None, qmap.get(q, f"qn{q}"), "内置音轨(渐进式)"
    raise RuntimeError("playurl 无可用流（dash/durl 均空）")


def _switch_episode(direction: int):
    """切到下一集(+1)/上一集(-1)。越界时保持原位。"""
    st = _EPISODE_STATE
    eps = st.get("episodes") or []
    idx = st.get("index", -1)
    if not eps or idx < 0:
        print("[*] 无剧集上下文，跳过切集。", flush=True)
        return

    target = idx + direction
    if target < 0 or target >= len(eps):
        edge = "开头" if direction < 0 else "末尾"
        print(f"[*] 已到{edge}，不切换。", flush=True)
        return

    ep = eps[target]
    ctx = st.get("ctx") or {}
    try:
        if ctx.get("pgc"):
            # 番剧：PGC API（免费账号渐进式 durl，大会员 DASH）
            result = get_playurl_pgc(ctx["session"], ep["bvid"], ep["cid"])
            vurl, aurl, vd, ad = pick_streams_from_result(result)
        else:
            data = get_playurl(ctx["session"], ep["bvid"], ep["cid"],
                               ctx["img_key"], ctx["sub_key"])
            dash = data.get("dash")
            if not dash:
                print("[!] 该集无 DASH 数据，跳过。", flush=True)
                return
            vurl, aurl, vd, ad = pick_dolby_streams(dash)
        if not vurl:
            print("[!] 该集无可播放视频流。", flush=True)
            return

        if not _ipc_loadfile(vurl, aurl, ep["title"]):
            print("[!] MPV IPC 连接失败，切集未生效。", flush=True)
            return

        st["index"] = target
        print(f"[+] 已切{'下一' if direction > 0 else '上一'}集: "
              f"{ep['title']} ({vd})", flush=True)
    except Exception as e:
        print(f"[!] 切集失败: {e}", flush=True)


def _handle_mpv_event(ev: dict):
    """处理 MPV IPC 事件：自动连播。
    实测确认：keep-open=yes 下 end-file 事件不发送，必须 observe eof-reached
    属性（EOF 到达时变 true）。end-file 仅作补充兜底。"""
    etype = ev.get("event")

    # 主信号：eof-reached 属性变化为 true（自动切下一集）
    if etype == "property-change" and ev.get("id") == 2 and ev.get("data") is True:
        _switch_episode(+1)
        return

    # 补充兜底：end-file reason=eof（非 keep-open 或部分场景仍会发）
    if etype == "end-file" and ev.get("reason") == "eof":
        _switch_episode(+1)


def _mpv_ipc_loop():
    """后台线程：连接 MPV 命名管道，observe eof-reached 实现自动连播。"""
    while True:
        h = _pipe_open(timeout_ms=3000)
        if not h:
            time.sleep(1.5)   # MPV 未启动 / 已退出，稍后重连
            continue
        try:
            # 观察 eof-reached 属性（id=2）：EOF 到达变 true → 自动切下一集
            payload = (json.dumps({"command": ["observe_property", 2,
                                               "eof-reached"]}) + "\n").encode()
            _pipe_write(h, payload)
            while True:
                msg = _pipe_read_message(h)
                if msg is None:
                    break   # 管道断开，重连
                line = msg.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                _handle_mpv_event(ev)
        except Exception:
            pass
        finally:
            _pipe_close(h)


_NAV_SIGNAL = _CONFIG_DIR / "bili-nav.signal"   # Lua 写入、Python 轮询的切集信号

def _nav_signal_loop():
    """后台线程：轮询切集信号文件（Lua 的 Ctrl+←/→ 写入），读后删除防重复。"""
    while True:
        try:
            if _NAV_SIGNAL.exists():
                try:
                    nav = _NAV_SIGNAL.read_text(encoding="utf-8").strip()
                    if nav == "next":
                        _switch_episode(+1)
                    elif nav == "prev":
                        _switch_episode(-1)
                finally:
                    try:
                        _NAV_SIGNAL.unlink()
                    except OSError:
                        pass
        except Exception:
            pass
        time.sleep(0.3)


def start_ipc_monitor():
    """启动 MPV IPC 监听线程（幂等）。"""
    global _IPC_THREAD, _SIGNAL_THREAD
    if _IPC_THREAD and _IPC_THREAD.is_alive():
        # IPC 线程活着即认为整套已启动，信号线程一并就绪
        return
    _IPC_THREAD = threading.Thread(target=_mpv_ipc_loop, daemon=True)
    _IPC_THREAD.start()
    if not (_SIGNAL_THREAD and _SIGNAL_THREAD.is_alive()):
        _SIGNAL_THREAD = threading.Thread(target=_nav_signal_loop, daemon=True)
        _SIGNAL_THREAD.start()


# ============================================================================
# 6. 唤起播放器
# ============================================================================

# 会话级跟踪当前 mpv 进程：新播放前清退旧实例，防双开 + IPC 管道冲突
_MPV_PROC = None


def _quit_previous_mpv(timeout: float = 2.0):
    """新播放前清退旧 mpv。

    优先走 IPC 管道发 quit（优雅退出：正常释放命名管道 + WASAPI 独占音频设备），
    覆盖两种旧实例：本会话启动的、以及上次应用退出留下的孤儿（孤儿仍占管道，
    能收到 quit）。确认管道释放后才返回；跟踪到的进程超时未死则强杀兜底。
    """
    global _MPV_PROC
    # 管道被占 = 有旧 mpv（含孤儿）在运行
    probe = _pipe_open(timeout_ms=200)
    if probe:
        _pipe_close(probe)
        _ipc_send_cmd(["quit"])
        deadline = time.time() + timeout
        while time.time() < deadline:
            h = _pipe_open(timeout_ms=100)
            if not h:
                break  # 管道已释放，旧 mpv 已退出
            _pipe_close(h)
            time.sleep(0.1)
    # 跟踪到的进程仍存活 → 强杀兜底
    proc = _MPV_PROC
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _MPV_PROC = None


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


# ---- YouTube 限流探测 ------------------------------------------------------
# 2026-10-19 实测（代理出口被 YouTube 标记的会话）：
# android_vr 等默认 client 拿到的播放地址，CDN（gvs）只放行每个 URL 的前
# ~2.5MB——无 Range / 开放 Range / 中段 Range 全部 403，mpv（固定发
# Range: bytes=0-）必然失败，表现为"mpv 窗口一闪即逝"。
# 换 player_client=android 拿到的地址不受此限制（代价：最高 360p muxed）。
# 本函数播放前探测一次（每会话缓存），决定 ytdl_hook 用哪个 client。

_YT_THROTTLED: bool | None = None


def _probe_url_range(url: str, start: int, end: int, proxy: str,
                     headers: dict = None) -> int:
    """经代理对 url 发一次有界 Range 请求，返回 HTTP 状态码。失败返回 0。"""
    import http.client
    try:
        parsed = urllib.parse.urlparse(url)
        p = urllib.parse.urlparse(proxy)
        conn = http.client.HTTPSConnection(
            p.hostname, p.port or 80, timeout=15
        )
        try:
            conn.set_tunnel(parsed.hostname, parsed.port or 443)
            hdrs = dict(headers or {})
            hdrs["Range"] = f"bytes={start}-{end}"
            hdrs.setdefault("Accept", "*/*")
            conn.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""),
                         headers=hdrs)
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()
    except Exception:
        return 0


def _yt_is_throttled(ytdlp_exe: str, page_url: str, proxy: str,
                     log=None) -> bool:
    """解析一次视频，抽中段范围探测 CDN 是否限流。异常时按不限流处理。"""
    global _YT_THROTTLED
    if _YT_THROTTLED is not None:
        return _YT_THROTTLED
    try:
        cmd = [ytdlp_exe, "--no-warnings", "-J", "--no-playlist",
               "--format", "bestvideo[height<=2160]+bestaudio/best"]
        if proxy:
            cmd.append(f"--proxy={proxy}")
        cmd += ["--", page_url]
        out = _run_silent(cmd, capture_output=True, text=True, timeout=90)
        if out.returncode != 0:
            _YT_THROTTLED = False
            return False
        data = json.loads(out.stdout)
        fmts = data.get("requested_formats") or [data]
        f = next((x for x in fmts if x.get("vcodec") != "none"), None) or fmts[0]
        url = f.get("url")
        size = f.get("filesize") or f.get("filesize_approx")
        if not url or not size or size < 4 * 1024 * 1024:
            _YT_THROTTLED = False
            return False
        mid = size // 2
        status = _probe_url_range(url, mid, mid + 1024 * 1024 - 1, proxy,
                                  f.get("http_headers"))
        _YT_THROTTLED = (status != 206)
        return _YT_THROTTLED
    except Exception:
        _YT_THROTTLED = False
        return False


def launch_player(player_path, video_url, title, audio_url=None, sessdata=None, log=None):
    """唤起 mpv 播放。B 站（有 sessdata）走 CDN 直链 + cookie；YouTube（无 sessdata）走 ytdl_hook + 代理。

    log: 可选回调函数，用于把状态/错误信息传给 GUI（避免 print 在 --windowed 模式下被吞）。
    """
    import tempfile

    def _emit(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass
        print(msg, flush=True)

    portable_conf = str(_exe_dir / "mpv-portable" / "portable_config")

    # ── 公共参数 ──────────────────────────────────────────────────────────
    cmd = [
        player_path,
        video_url,
        f"--force-media-title={title}",
        f"--config-dir={portable_conf}",
        "--log-file=" + str(_CONFIG_DIR / "mpv.log"),
        # MPV IPC：切集通道（命名管道，< / > 切集 + 播完自动下一集）
        f"--input-ipc-server={IPC_PIPE}",
    ]

    # 注入切集快捷键转发脚本（Lua，绑定 Ctrl+←/Ctrl+→ → 写信号文件）
    lua_script = str(_exe_dir / "bili-ipc.lua")
    if os.path.isfile(lua_script):
        cmd += [f"--script={lua_script}"]

    # ── B 站：CDN 直链 + cookie + DASH 音视频分离 ──────────────────────────
    if sessdata:
        cookie_fd, cookie_path = tempfile.mkstemp(
            suffix=".txt", prefix="bili_cookies_", text=True
        )
        with os.fdopen(cookie_fd, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write(".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\t" + sessdata + "\n")
        atexit.register(lambda p=cookie_path: os.unlink(p) if os.path.exists(p) else None)
        cmd += [
            "--cookies-file=" + cookie_path,
            "--http-header-fields=Referer: " + REFERER,
            "--user-agent=" + UA,
        ]
        if audio_url:
            # 仅挂外部音频文件；m4s 网络流由 mpv 自动探测，
            # 不强制 --audio-demuxer / --load-scripts（会干扰音轨加载与 Lua 脚本）
            cmd += [
                f"--audio-file={audio_url}",
            ]

    # ── YouTube：ytdl_hook 解析 + 走系统代理 ──────────────────────────────
    else:
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
        # 限流会话探测：默认 client 的地址被 CDN 锁前 2.5MB 时换 android client
        if _yt_is_throttled(ytdlp_exe, video_url, ytdlp_proxy or "", _emit):
            ytdl_opts += ",extractor-args=youtube:player_client=android"
            _emit("    [!] 检测到 YouTube 限流，切换 android client（最高 360p）")
        cmd += [
            f"--script-opts=ytdl_hook-ytdl_path={ytdlp_exe}",
            "--ytdl-format=bestvideo[height<=2160]+bestaudio/best",
            f"--ytdl-raw-options={ytdl_opts}",
        ]

    _emit(f"    唤起 mpv: {title}")
    # 先清退旧 mpv（双开会抢 IPC 管道导致切集/连播失效，且双窗口混播）
    _quit_previous_mpv()
    # 先发媒体暂停键让浏览器视频暂停,再启动 mpv(避免 mpv 也收到暂停)
    _pause_browser_media()
    time.sleep(0.2)
    # 启动 mpv: 只需抑制控制台弹窗(CREATE_NO_WINDOW), 不能用 SW_HIDE(会隐藏 mpv 主窗口!)
    popen_kw = {}
    if sys.platform == "win32":
        popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, **popen_kw)
    global _MPV_PROC
    _MPV_PROC = proc

    # 启动 IPC 监听线程：支持 < / > 切集 + 播完自动下一集（幂等）
    start_ipc_monitor()

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
