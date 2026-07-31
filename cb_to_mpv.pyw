#!/usr/bin/env python3
"""
cb_to_mpv.pyw  —  极简剪贴板监听 → mpv 播放

Windows 下无控制台黑框。复制 B 站 / YouTube 链接，自动拉起 mpv。
一切解析（WBI 签名 / DASH 分轨 / yt-dlp）交给 mpv 内置机制。

零第三方依赖，仅使用 Python 标准库。

便携模式：
  - 同级目录的 mpv.exe 优先
  - 其次查找 mpv/mpv.exe
  - 可通过 config.ini 手动指定路径
  - cookies.txt 不存在时自动创建空白文件
"""

import configparser
import ctypes
import ctypes.wintypes
import re
import subprocess
import sys
import time
from pathlib import Path

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
# 配置加载
# ============================================================================

CONFIG_PATH = SCRIPT_DIR / "config.ini"
_cfg = configparser.ConfigParser()

def _load_config():
    """加载 config.ini（可选��。"""
    if CONFIG_PATH.is_file():
        _cfg.read(CONFIG_PATH, encoding="utf-8")

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

def _resolve_cookies() -> str:
    """获取 cookies.txt 路径。不存在则自动创建带说明的空白文件。"""
    p = SCRIPT_DIR / "cookies.txt"

    # config.ini 覆盖
    val = _cfg_get("paths", "cookies_file")
    if val:
        p = Path(val)

    if not p.is_file():
        p.write_text(
            "# B 站 / YouTube Cookie 文件\n"
            "#\n"
            "# 方法一（推荐）：在 config.ini 中设置 BROWSER_COOKIE = edge（或 chrome / firefox）\n"
            "# 方法二：安装插件 Get cookies.txt LOCALLY，导出内容粘贴到此文件\n"
            "#\n"
            "# 留空则使用游客模式（B 站 1080P / YouTube 普通画质）。\n"
            "#\n"
            ,
            encoding="utf-8",
        )
        return ""  # 空白文件不传入 mpv

    # 检查是否只有注释（用户还没填入真实 Cookie）
    content = p.read_text(encoding="utf-8").strip()
    if not content or content.startswith("#"):
        return ""

    return str(p)


def _resolve_browser_cookie() -> str:
    """从 config.ini 读取浏览器名称（edge / chrome / firefox 等）。"""
    return _cfg_get("cookies", "browser_cookie")


# ============================================================================
# 代理
# ============================================================================

def _resolve_proxy() -> str:
    """从 config.ini 读取代理。"""
    return _cfg_get("network", "proxy")


# ============================================================================
# 链接正则
# ============================================================================

BILI_RE = re.compile(
    r"https?://(?:www\.)?bilibili\.com/"
    r"(?:video/(BV[a-zA-Z0-9]+)|bangumi/play/(ep\d+|ss\d+)|bangumi/media/(md\d+))"
)

YT_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)"
    r"([a-zA-Z0-9_-]{11})"
    r"(?:[?&/].*)?"
)


# ============================================================================
# Win32 剪贴板读取
# ============================================================================

def _get_clipboard_text() -> str:
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
            return ""
        if not user32.IsClipboardFormatAvailable(1):
            user32.CloseClipboard()
            return ""
        h_data = user32.GetClipboardData(1)
        if not h_data:
            user32.CloseClipboard()
            return ""
        p_data = kernel32.GlobalLock(h_data)
        if not p_data:
            user32.CloseClipboard()
            return ""
        try:
            size = kernel32.GlobalSize(h_data)
            if size > 4096:
                return ""
            buf = ctypes.create_string_buffer(size)
            ctypes.memmove(buf, p_data, size)
            return buf.value.decode("utf-8", errors="ignore")
        finally:
            kernel32.GlobalUnlock(h_data)
        user32.CloseClipboard()
        return ""
    except Exception:
        return ""


# ============================================================================
# mpv 启动
# ============================================================================

def _launch_mpv(url: str, mpv_path: str, ytdlp_path: str,
                mpv_confdir: str, cookies_file: str, browser_cookie: str,
                proxy: str):
    cmd = [mpv_path, url]

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

    cmd.append("--ytdl-format=bestvideo[height<=2160]+bestaudio/best")

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except FileNotFoundError:
        print(f"[!] 找不到 mpv: {mpv_path}")
    except Exception as e:
        print(f"[!] 拉起 mpv 失败: {e}")


# ============================================================================
# 链接提取
# ============================================================================

def _extract_url(text: str) -> str | None:
    m = BILI_RE.search(text)
    if m:
        start, end = m.start(), m.end()
        while end < len(text) and text[end] not in ("\n", "\r", " "):
            end += 1
        full_url = text[start:end]
        if not full_url.startswith("http"):
            full_url = "https://" + full_url.lstrip("/")
        full_url = full_url.split("?", 1)[0] if "?" in full_url else full_url
        return full_url

    m = YT_RE.search(text)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"

    return None


# ============================================================================
# 主循环
# ============================================================================

def main():
    # ── 路径解析 ──
    mpv_path    = _resolve_mpv()
    ytdlp_path  = _resolve_ytdlp()
    mpv_confdir = _resolve_mpv_confdir()
    cookies_file   = _resolve_cookies()
    browser_cookie = _resolve_browser_cookie()
    proxy          = _resolve_proxy()

    print(f"[+] mpv:      {mpv_path}")
    if ytdlp_path:
        print(f"[+] yt-dlp:   {ytdlp_path}")
    if mpv_confdir:
        print(f"[+] config:   {mpv_confdir}")
    if proxy:
        print(f"[+] 代理:    {proxy}")
    if cookies_file:
        print(f"[+] Cookie:   {cookies_file}")
    elif browser_cookie:
        print(f"[+] Cookie:   cookies-from-browser={browser_cookie}")
    else:
        print(f"[*] Cookie:   未配置（游客模式，B 站 1080P / YouTube 普通画质）")

    print("[*] 监听中… 复制 B 站 / YouTube 链接即可播放。")

    last_url = ""
    while True:
        try:
            text = _get_clipboard_text()
            url = _extract_url(text)
            if url and url != last_url:
                last_url = url
                print(f">> {url}")
                _launch_mpv(url, mpv_path, ytdlp_path, mpv_confdir,
                            cookies_file, browser_cookie, proxy)
            time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n[*] 已退出。")
            break
        except Exception as e:
            print(f"[!] {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
