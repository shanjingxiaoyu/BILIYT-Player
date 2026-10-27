#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sponsorblock.py — BilibiliSponsorBlock 片段查询与跳过载荷构造

职责边界（见 .cursorrules）：
  - 本模块负责「查什么」：请求 bsbsb.top API、过滤分类、构造 JSON 载荷、落盘 Lua 脚本。
  - mpv 的 Lua 脚本负责「怎么跳」：比对 time-pos 并执行 seek。
  - 本模块绝不实现任何 seek / 渲染 / 快捷键逻辑。

设计要点：
  - API 无数据时返回 200 + []（不是 404），故一律按「空列表」处理。
  - actionType 只有 "skip"/"mute" 可执行；"full" 是整片推广标签（segment=[0,0]），
    若不过滤会导致跳转到 0 秒，必须丢弃。"poi" 是高光跳转，非广告，同样丢弃。
  - 载荷经环境变量传递而非 --script-opts：script-opts 以逗号分隔，
    而 JSON 必然含逗号，会被截断。
  - 所有网络异常一律吞掉返回 []，保证绝不影响正常播放。
"""

import json
import os
import socket
import ssl
from pathlib import Path

__all__ = [
    "DEFAULT_SERVER",
    "DEFAULT_CATEGORIES",
    "ALL_CATEGORIES",
    "TIERS",
    "CATEGORY_TIERS",
    "CATEGORY_COLORS",
    "PAYLOAD_ENV",
    "SCRIPT_NAME",
    "SKIP_SCRIPT_LUA",
    "load_config",
    "save_config",
    "parse_categories",
    "format_categories",
    "fetch_segments",
    "filter_segments",
    "build_payload",
    "build_osc_payload",
    "ensure_lua_script",
    "report_view",
    "MAX_SEGMENTS",
]

# ============================================================================
# 常量
# ============================================================================

DEFAULT_SERVER = "https://bsbsb.top"

#: 全部已知分类（顺序即 GUI 展示顺序，与原作者插件一致）
ALL_CATEGORIES = (
    "sponsor",
    "selfpromo",
    "exclusive_access",
    "interaction",
    "poi_highlight",
    "intro",
    "outro",
    "preview",
    "filler",
    "music_offtopic",
    "padding",
)

#: 分类中文名（GUI 与文档使用）
CATEGORY_NAMES = {
    "sponsor": "赞助/恰饭",
    "selfpromo": "无偿/自我推广",
    "exclusive_access": "独家访问/抢先体验",
    "interaction": "三连/互动提醒",
    "poi_highlight": "精彩时刻/重点",
    "intro": "过场/开场动画",
    "outro": "鸣谢/结束画面",
    "preview": "回顾/概要",
    "filler": "填充内容/前黑/后黑",
    "music_offtopic": "音乐:非音乐部分",
    "padding": "片尾填充",
}

#: 分类说明（GUI 提示用，取自原作者插件的分类含义）
CATEGORY_DESCS = {
    "sponsor": "付费推广、推荐和直接广告，不是自我推广或免费提及",
    "selfpromo": "无偿/自我推广，包括有关商品、捐赠或合作者的信息",
    "exclusive_access": "仅用于对整段视频进行标记，例如展示 UP 主免费或获得补贴后使用的产品",
    "interaction": "视频中简短提醒观众一键三连或关注",
    "poi_highlight": "大部分人在寻找的空降时间点，相当于「封面在 12:34」",
    "intro": "没有实际内容的间隔片段，可以是暂停、静态帧或重复动画",
    "outro": "致谢画面或片尾画面，不包含内容的结尾",
    "preview": "展示此视频或同系列视频将出现的画面集锦",
    "filler": "搬运视频片头片尾的纯粹填充内容，如黑屏或无关画面",
    "music_offtopic": "仅作为填充内容或增添趣味而添加的离题片段",
    "padding": "片尾或片头无实际意义的填充部分",
}

#: 四档跳过行为
#:   auto       自动跳过（进入片段即跳，可撤销）
#:   manual     手动跳过（进度条显色，按 n 跳）
#:   bar        仅在进度条显示，不跳过
#:   prohibited 完全禁用（不查询、不显示）
TIERS = ("auto", "manual", "bar", "prohibited")

#: 分类默认档位（对齐原作者插件的默认配置）
CATEGORY_TIERS = {
    "sponsor": "auto",
    "selfpromo": "auto",
    "exclusive_access": "bar",       # 原插件为「显示标签」
    "interaction": "manual",
    "poi_highlight": "manual",       # 原插件为「视频加载时询问」
    "intro": "manual",
    "outro": "manual",
    "preview": "bar",                # 原插件为「在进度条中显示」
    "filler": "auto",
    "music_offtopic": "auto",
    "padding": "auto",
}

#: 分类配色（ASS 的 &HAABBGGRR&，AA 为透明度，FF 最透明；这里只给 BBGGRR）
#: 取自原作者插件的进度条颜色，便于用户与浏览器插件对照
CATEGORY_COLORS = {
    "sponsor": "&H0000D400&",        # 绿
    "selfpromo": "&H0000FFFF&",      # 黄
    "exclusive_access": "&H0000C000&",  # 深绿
    "interaction": "&H00FF00FF&",    # 品红
    "poi_highlight": "&H008000FF&",  # 橙红
    "intro": "&H00FFFF00&",          # 青
    "outro": "&H00FF0000&",          # 蓝
    "preview": "&H00FF8000&",        # 天蓝
    "filler": "&H00404040&",         # 深灰(前黑/后黑)
    "music_offtopic": "&H000080FF&",  # 橙
    "padding": "&H00606060&",        # 灰
}

#: 默认档位（向后兼容旧配置里的 categories 逗号列表）
DEFAULT_CATEGORIES = tuple(
    c for c, t in CATEGORY_TIERS.items() if t == "auto"
)

#: 需要实际跳过的档位
ACTIONABLE_TIERS = ("auto", "manual")

#: 需要显示在进度条上的档位（auto/manual/bar 都要显示色块）
VISIBLE_TIERS = ("auto", "manual", "bar")

#: 向 mpv 传递 JSON 载荷的环境变量名
PAYLOAD_ENV = "BSPONSOR_PAYLOAD"

#: 落盘的 Lua 脚本文件名（写入配置目录，不在 mpv 的 scripts/ 自动加载目录内）
SCRIPT_NAME = "bsponsor.lua"

#: 载荷上限，防止异常大的响应拖慢 mpv 启动（约 20 KB）
MAX_SEGMENTS = 200

#: 只有这两种动作可以执行
_ACTIONABLE = ("skip", "mute")

#: 请求头：标注调用来源，便于社区统计（服务端不强制要求）
_CLIENT_HEADERS = {
    "origin": "BiliYTPlayer",
    "x-ext-version": "0.1.0",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}


# ============================================================================
# 配置
# ============================================================================

_DEFAULT_CONFIG = {
    "enabled": True,
    "categories": "sponsor:auto",   # 占位，真实默认值在下方 _build_default_config()
    "behavior": "skip",          # skip | mute
    "notify": True,              # 跳过时显示提示 + 倒计时
    "undo_seconds": 3.0,         # 自动跳过后的撤销窗口
    "min_duration": 1.0,         # 短于此长度的片段忽略（秒）
    "server": DEFAULT_SERVER,
    "timeout": 2.5,              # 查询超时（秒），必须短，避免拖慢起播
    "report_views": False,       # 是否上报「已跳过」统计
    "debug": False,
}


_CONFIG_TEMPLATE = """\
# ==============================================================================
# BiliYTPlayer — SponsorBlock（广告跳过）配置
# 数据来源：https://bsbsb.top  （BilibiliSponsorBlock 公共服务器）
#
# 建议用 GUI 的「跳过设置」按钮修改本文件，避免格式出错。
# 改完配置后，播放下一个视频即生效（无需重启）。
# ==============================================================================

# 是否启用广告跳过。false = 完全不查询 API，行为与未接入前一致
enabled = true

# ── 分类跳过档位 ─────────────────────────────────────────────────────────────
# 每个分类四种档位：
#   auto        自动跳过（进入片段即跳，3 秒内可按 Enter 撤销）
#   manual      手动跳过（进度条显色，按 n 键跳）
#   bar         仅在进度条显示色块，不跳过
#   prohibited  完全禁用
categories = sponsor:auto, selfpromo:auto, exclusive_access:bar, interaction:manual, poi_highlight:manual, intro:manual, outro:manual, preview:bar, filler:auto, music_offtopic:auto, padding:auto

# 命中片段时的动作：skip = 跳过；mute = 静音但不跳过
behavior = skip

# 跳到片段时是否在画面上显示提示（含 3 秒撤销倒计时）
notify = true

# 自动跳过时，允许按 Enter 撤销的秒数
undo_seconds = 3.0

# 短于该秒数的片段忽略（防止抖动）
min_duration = 1.0

# API 服务器地址
server = https://bsbsb.top

# 查询超时（秒）。建议不超过 3，否则会拖慢起播
timeout = 2.5

# 是否向服务器上报「已跳过」次数（仅统计，默认关闭以保护隐私）
report_views = false

# 输出调试日志到 mpv.log
debug = false
"""


def _config_path(config_dir: Path) -> Path:
    return Path(config_dir) / "sponsorblock.conf"


def _coerce(key: str, raw: str):
    """把 .conf 中的字符串按默认值类型转换。"""
    default = _DEFAULT_CONFIG[key]
    raw = raw.strip()
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            return default
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    return raw


def parse_categories(raw) -> dict:
    """解析 categories 配置 → {category: tier}。

    兼容两种写法：
      新：`sponsor:auto, intro:manual`   （分类:档位）
      旧：`sponsor,selfpromo`            （裸分类名 → 视为 auto，便于向后兼容）
    未知分类、未知档位一律忽略；返回的字典只含合法项。
    """
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items()
                if k in ALL_CATEGORIES and v in TIERS}
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = str(raw or "").replace(";", ",").split(",")

    out = {}
    for item in items:
        name = str(item).strip()
        if not name:
            continue
        if ":" in name:
            cat, _, tier = name.partition(":")
            cat, tier = cat.strip(), tier.strip().lower()
        else:
            cat, tier = name, "auto"   # 旧格式：裸分类名 = 自动跳过
        if cat in ALL_CATEGORIES and tier in TIERS:
            out[cat] = tier
    return out


def format_categories(tiers) -> str:
    """把 {category: tier} 序列化成配置字符串（固定顺序，便于 diff）。"""
    if not isinstance(tiers, dict):
        tiers = parse_categories(tiers)
    parts = [f"{c}:{tiers[c]}" for c in ALL_CATEGORIES if c in tiers]
    return ", ".join(parts)


def active_categories(tiers) -> tuple:
    """需要向 API 查询的分类（档位非 prohibited 的，或旧格式裸名）。"""
    if not isinstance(tiers, dict):
        tiers = parse_categories(tiers)
    return tuple(c for c, t in tiers.items() if t != "prohibited")


def load_config(config_dir: Path, log=None) -> dict:
    """读取 sponsorblock.conf；不存在则写入模板并返回默认值。

    任何读取/解析异常都回退到默认值，绝不抛出。
    """
    cfg = dict(_DEFAULT_CONFIG)
    cfg["category_tiers"] = dict(CATEGORY_TIERS)
    path = _config_path(config_dir)

    if not path.exists():
        try:
            Path(config_dir).mkdir(parents=True, exist_ok=True)
            path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
            if log:
                log(f"[*] 已生成 SponsorBlock 配置模板: {path}")
        except OSError:
            pass
        return cfg

    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";", "[")) or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key in _DEFAULT_CONFIG:
                cfg[key] = _coerce(key, value)
    except (OSError, UnicodeDecodeError) as e:
        if log:
            log(f"[!] SponsorBlock 配置读取失败，使用默认值: {e}")
        cfg["category_tiers"] = dict(CATEGORY_TIERS)
        return cfg

    # 分类档位：解析失败（空/全非法）时回落到默认
    tiers = parse_categories(cfg.get("categories", ""))
    cfg["category_tiers"] = tiers or dict(CATEGORY_TIERS)
    return cfg


def save_config(config_dir: Path, cfg: dict) -> bool:
    """把配置回写到 sponsorblock.conf（保留模板注释结构）。"""
    path = _config_path(config_dir)
    try:
        Path(config_dir).mkdir(parents=True, exist_ok=True)
        tiers = cfg.get("category_tiers")
        lines = []
        for line in _CONFIG_TEMPLATE.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                lines.append(line)
                continue
            key, _, _ = stripped.partition("=")
            key = key.strip()
            if key == "categories" and isinstance(tiers, dict):
                lines.append(f"categories = {format_categories(tiers)}")
                continue
            if key in cfg:
                value = cfg[key]
                if isinstance(value, bool):
                    value = "true" if value else "false"
                lines.append(f"{key} = {value}")
            else:
                lines.append(line)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def categories_of(cfg: dict) -> tuple:
    """需要执行跳过（auto + manual）的分类元组。"""
    tiers = cfg.get("category_tiers")
    if not isinstance(tiers, dict):
        tiers = parse_categories(cfg.get("categories", ""))
    return tuple(c for c, t in tiers.items() if t in ACTIONABLE_TIERS)


# 真实默认分类串（需在 format_categories 定义之后才能求值）
_DEFAULT_CONFIG["categories"] = format_categories(CATEGORY_TIERS)


# ============================================================================
# 网络查询
# ============================================================================

def _http_get_json(url: str, timeout: float, proxy: str = None) -> object:
    """GET 并解析 JSON。

    优先 requests 且 trust_env=False（绕开本机代理探测开销）；
    requests 不可用时回退裸 socket；均失败则抛出，由调用方兜底。
    """
    try:
        import requests  # 延迟导入：本模块在 mpv 启动路径上被调用，尽早失败更好

        session = requests.Session()
        session.trust_env = False  # 关键：避免 urllib3 在 Windows 上探测系统代理
        proxies = {"http": proxy, "https": proxy} if proxy else None
        resp = session.get(url, headers=_CLIENT_HEADERS, timeout=timeout, proxies=proxies)
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        pass

    # 回退：裸 socket 直连（与 bili_clipboard_dolby._fast_bili_api 同思路）
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((socket.gethostbyname(host), 443 if parsed.scheme == "https" else 80))
        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            conn = ctx.wrap_socket(sock, server_hostname=host)
        else:
            conn = sock
        req = f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
        for k, v in _CLIENT_HEADERS.items():
            req += f"{k}: {v}\r\n"
        req += "Accept: application/json\r\nConnection: close\r\n\r\n"
        conn.sendall(req.encode())
        data = b""
        while True:
            chunk = conn.recv(8192)
            if not chunk:
                break
            data += chunk
        conn.close()
    finally:
        try:
            sock.close()
        except OSError:
            pass

    _, _, body = data.partition(b"\r\n\r\n")
    if b"chunked" in data.split(b"\r\n\r\n", 1)[0].lower():
        decoded, pos = b"", 0
        while pos < len(body):
            line_end = body.find(b"\r\n", pos)
            if line_end == -1:
                break
            size = int(body[pos:line_end], 16)
            if size == 0:
                break
            pos = line_end + 2
            decoded += body[pos:pos + size]
            pos += size + 2
        body = decoded
    return json.loads(body)


def fetch_segments(bvid: str, cid=None, *, server: str = None, timeout: float = None,
                   proxy: str = None, log=None) -> list:
    """查询某视频（可选指定分 P）的片段列表。

    返回已归一化的 dict 列表；**任何异常都返回空列表，绝不抛出**。
    归一化字段：start, end, category, actionType, uuid, cid
    """
    base = (server or DEFAULT_SERVER).rstrip("/")
    url = f"{base}/api/skipSegments?videoID={bvid}"
    if cid is not None and str(cid) != "":
        url += f"&cid={cid}"

    def _note(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass

    try:
        raw = _http_get_json(url, float(timeout or _DEFAULT_CONFIG["timeout"]), proxy)
    except Exception as e:
        _note(f"    [!] SponsorBlock 查询失败（已忽略）: {type(e).__name__}: {e}")
        return []

    # 服务端无数据时返回 200 + []（而非文档所述的 404）
    if not isinstance(raw, list):
        _note("    [!] SponsorBlock 返回格式异常（已忽略）")
        return []

    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        seg = item.get("segment")
        if not (isinstance(seg, (list, tuple)) and len(seg) >= 2):
            continue
        try:
            start, end = float(seg[0]), float(seg[1])
        except (TypeError, ValueError):
            continue
        out.append({
            "start": start,
            "end": end,
            "category": str(item.get("category") or ""),
            "actionType": str(item.get("actionType") or "skip"),
            "uuid": str(item.get("UUID") or ""),
            "cid": str(item.get("cid") or ""),
        })
    return out


def filter_segments(segments, categories, min_duration: float = 1.0,
                    tiers=None) -> list:
    """过滤并合并片段，并附加每段的档位与配色。

    categories: 允许保留的分类（通常来自 active_categories，即非 prohibited）。
    tiers:      {category: tier}，用于给每段附上 tier 与 color；
                为 None 时按 categories 推断为 auto。

    丢弃：非 skip/mute 动作（含 full / poi）、end<=start、过短、分类不符。
    合并：仅合并**同档位**的相邻区间 —— 不同档位（如 auto 与 bar）必须保持独立，
          否则会在进度条上把不同颜色的区间糊成一块。
    """
    allowed = set(categories or ())
    tiers = tiers if isinstance(tiers, dict) else {}
    all_tiers = dict(CATEGORY_TIERS)
    all_tiers.update(tiers)

    cleaned = []
    for seg in segments or []:
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue

        action = str(seg.get("actionType") or "skip")
        if action not in _ACTIONABLE:
            # "full" 是整片推广标签（segment=[0,0]），"poi" 是高光跳转：
            # 两者都不能当作跳过区间处理，否则会跳回 0 秒或跳到无关位置。
            continue
        if end <= start:
            continue  # [0,0] 之类的空区间
        if (end - start) < float(min_duration):
            continue

        cat = str(seg.get("category") or "")
        if allowed and cat not in allowed:
            continue

        tier = all_tiers.get(cat, "auto")
        if tier == "prohibited":
            continue

        cleaned.append({
            "start": start,
            "end": end,
            "category": cat,
            "actionType": action,
            "uuid": str(seg.get("uuid") or seg.get("UUID") or ""),
            "tier": tier,
            "color": CATEGORY_COLORS.get(cat, "&H00D400&"),
        })

    # 合并时把档位纳入排序键，保证同档位区间相邻后再合并
    cleaned.sort(key=lambda s: (s["start"], s["end"], s["tier"]))

    merged = []
    for seg in cleaned:
        prev = merged[-1] if merged else None
        if (prev and prev["tier"] == seg["tier"]
                and seg["start"] - prev["end"] < 0.5):
            prev["end"] = max(prev["end"], seg["end"])
        else:
            merged.append(dict(seg))

    merged.sort(key=lambda s: s["start"])
    return merged


def build_payload(bvid: str, cid, segments, cfg: dict, title: str = "") -> str:
    """构造传给 mpv Lua 的 JSON 载荷（UTF-8，不含 BOM）。

    同时供 bsponsor.lua（执行跳过）与受管 osc.lua（画进度条）读取。
    """
    payload = {
        "version": 2,
        "bvid": str(bvid or ""),
        "cid": str(cid if cid is not None else ""),
        "behavior": cfg.get("behavior", "skip"),
        "notify": bool(cfg.get("notify", True)),
        "undo_seconds": float(cfg.get("undo_seconds", 3.0)),
        "debug": bool(cfg.get("debug", False)),
        "segments": [
            {
                "start": round(float(s["start"]), 3),
                "end": round(float(s["end"]), 3),
                "finish": round(float(s["end"]), 3),   # osc.lua 用 finish
                "category": s.get("category", ""),
                "actionType": s.get("actionType", "skip"),
                "tier": s.get("tier", "auto"),
                "color": s.get("color") or CATEGORY_COLORS.get(
                    s.get("category", ""), "&H00D400&"),
                "uuid": s.get("uuid", ""),
            }
            for s in list(segments or [])[:MAX_SEGMENTS]
        ],
    }
    if title:
        payload["title"] = title
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_osc_payload(segments, cfg: dict = None) -> str:
    """构造只给受管 osc.lua 用的精简载荷。

    受管 OSC 与跳过脚本各自独立读取载荷（前者画进度条，后者执行 seek），
    因此这里只保留画图必需的字段。
    """
    cfg = cfg or {}
    return json.dumps({
        "version": 2,
        "debug": bool(cfg.get("debug", False)),
        "segments": [
            {
                "start": round(float(s["start"]), 3),
                "finish": round(float(s["end"]), 3),
                "color": s.get("color") or CATEGORY_COLORS.get(
                    s.get("category", ""), "&H00D400&"),
                "category": s.get("category", ""),
            }
            for s in list(segments or [])[:MAX_SEGMENTS]
        ],
    }, ensure_ascii=False, separators=(",", ":"))


def report_view(uuid: str, *, server: str = None, timeout: float = None) -> bool:
    """上报一次「已跳过」，用于社区统计。默认不启用。"""
    if not uuid:
        return False
    base = (server or DEFAULT_SERVER).rstrip("/")
    url = f"{base}/api/viewedVideoSponsorTime"
    try:
        import requests

        session = requests.Session()
        session.trust_env = False
        resp = session.post(url, json={"UUID": uuid}, headers=_CLIENT_HEADERS,
                            timeout=float(timeout or 3.0))
        return resp.status_code < 400
    except Exception:
        return False


# ============================================================================
# Lua 跳过脚本（内嵌，运行时落盘）
# ============================================================================

# 说明：mpv 无内置 HTTP 客户端（mp 表无 socket API），因此网络请求必须由
# Python 完成，Lua 只负责执行 seek。载荷经环境变量传入，避免 --script-opts
# 的逗号分隔导致的 JSON 截断。
SKIP_SCRIPT_LUA = r'''-- bsponsor.lua — BilibiliSponsorBlock 片段跳过
-- 由 BiliYTPlayer 自动生成，请勿手工修改（改配置请编辑 sponsorblock.conf）。
--
-- 载荷来源：环境变量 BSPONSOR_PAYLOAD（JSON）
-- 可选覆盖：config-dir/script-opts/bsponsor.conf 或 --script-opts=bsponsor-*

local mp = require "mp"
local mo = require "mp.options"
local utils = require "mp.utils"

local opts = {
  behavior = "",     -- 空 = 用载荷里的值
  notify = nil,
  debug = nil,
  delay = 0.3,       -- 起播后延迟多久开始判定，避免 time-pos 抖动误判
}
mo.read_options(opts, "bsponsor")

-- ---- 读取载荷 ----------------------------------------------------------
local payload_raw = os.getenv("BSPONSOR_PAYLOAD")
if not payload_raw or #payload_raw == 0 then
  return
end

local ok, payload = pcall(utils.parse_json, payload_raw)
if not ok or type(payload) ~= "table" then
  mp.log("warn", "[bsponsor] 载荷解析失败，跳过功能未启用")
  return
end

local behavior = (opts.behavior ~= "" and opts.behavior) or payload.behavior or "skip"
local notify = opts.notify
if notify == nil then notify = payload.notify ~= false end
local debug = opts.debug
if debug == nil then debug = payload.debug == true end
local undo_seconds = tonumber(payload.undo_seconds) or 3.0

local function log(msg)
  if debug then mp.log("info", "[bsponsor] " .. msg) end
end

-- ---- 归一化片段 --------------------------------------------------------
-- 注意：JSON 字段名 "end" 与 Lua 保留字 end 冲突，必须用 s["end"] 取值，
-- 写成 s.end 会直接语法错误（"'<name>' expected near 'end'"）。
local segments = {}
for _, s in ipairs(payload.segments or {}) do
  local a, b = tonumber(s["start"]), tonumber(s["end"])
  local action = s["actionType"] or "skip"
  local tier = s["tier"] or "auto"
  -- 双保险：Python 已过滤，这里再挡一次 [0,0] / full / poi
  if a and b and b > a and (action == "skip" or action == "mute")
     and (tier == "auto" or tier == "manual") then
    segments[#segments + 1] = {
      start = a, finish = b,
      category = s["category"] or "?",
      action = action,
      tier = tier,
      uuid = s["uuid"],
    }
  end
end
table.sort(segments, function(x, y) return x.start < y.start end)

if #segments == 0 then
  log("无片段，功能空转")
  return
end
log(string.format("已加载 %d 个片段 behavior=%s undo=%ss", #segments, behavior, undo_seconds))

-- ---- 跳过引擎 ----------------------------------------------------------
-- 交互顺序（按用户要求）：先跳过 → 再提示 + 倒计时 → 期间按 Enter 可撤销
local fired = {}            -- 每个区间只触发一次
local muted_by_us = false
local armed_at = nil        -- 起播稳定时间戳

-- 撤销状态
local undo = {
  active = false,           -- 是否处于可撤销窗口
  until_time = 0,           -- 撤销截止（mp.get_time()）
  seg = nil,                -- 被跳过的片段
  from = 0,                 -- 跳过前的播放位置（撤销时回到这里）
}

local function disarm_all()
  fired = {}
  undo.active = false
  undo.seg = nil
  if muted_by_us then
    mp.set_property_native("mute", false)
    muted_by_us = false
  end
end

local function find_next(pos)
  for i, s in ipairs(segments) do
    if s.start > pos then return i end
  end
  return nil
end

-- 撤销：回到跳过前的位置，并把该片段标记为「本次不再跳」
local function do_undo()
  if not undo.active then
    if notify then mp.osd_message("没有可撤销的跳过", 1.2) end
    return
  end
  local seg = undo.seg
  local back = undo.from
  undo.active = false
  log(string.format("UNDO -> 回到 %.3f (%s)", back, seg and seg.category or "?"))
  mp.commandv("seek", tostring(back), "absolute+exact")
  if notify then
    mp.osd_message("已撤销跳过：" .. (seg and seg.category or ""), 1.5)
  end
  -- 撤销后该片段不再自动跳过（用户明确表示要看）
  if seg then seg.cancelled = true end
end

local function tick()
  if armed_at == nil then return end
  if mp.get_property_native("time-pos") == nil then return end

  local now = mp.get_time()
  if now - armed_at < opts.delay then return end

  local pos = mp.get_property_number("time-pos")
  if not pos then return end

  -- 撤销窗口维护：先跳过，再给 undo_seconds 秒反悔机会
  if undo.active then
    local left = undo.until_time - now
    if left <= 0 then
      undo.active = false
      log("撤销窗口结束")
    elseif notify then
      -- 持续刷新提示，让用户看到剩余时间
      mp.osd_message(string.format("已跳过 %s（%d 秒内按 Enter 撤销）",
                                   undo.seg and undo.seg.category or "",
                                   math.ceil(left)), 0.3)
    end
    return
  end

  local inside_any = false

  for i, s in ipairs(segments) do
    local inside = (pos >= s.start and pos < s.finish)
    if inside then inside_any = true end

    if inside and not fired[i] then
      fired[i] = true

      -- manual 档位：不自动跳，仅提示可手动跳
      if s.tier == "manual" then
        if notify then
          mp.osd_message("片段可跳过：" .. s.category .. "（按 n 跳过）", 2)
        end
        log(string.format("MANUAL hint at %.3f (%s)", pos, s.category))

      elseif s.action == "skip" and behavior == "skip" and not s.cancelled then
        -- 先跳，再进入可撤销窗口
        log(string.format("SKIP %.3f -> %.3f (%s)", pos, s.finish, s.category))
        undo.active = true
        undo.until_time = now + undo_seconds
        undo.seg = s
        undo.from = pos
        mp.commandv("seek", tostring(s.finish), "absolute+exact")
        if notify then
          mp.osd_message(string.format("已跳过 %s（%d 秒内按 Enter 撤销）",
                                       s.category, math.ceil(undo_seconds)), 2)
        end

      elseif s.action == "mute" or behavior == "mute" then
        log(string.format("MUTE %.3f -> %.3f (%s)", pos, s.finish, s.category))
        mp.set_property_native("mute", true)
        muted_by_us = true
        if notify then
          mp.osd_message("已静音 " .. s.category, 2)
        end
      end

    elseif fired[i] and pos < s.start - 1.0 then
      -- 用户手动拖回区间之前：重新布防，保证回看时仍会跳过
      fired[i] = nil
      s.cancelled = nil
    end
  end

  if muted_by_us and not inside_any then
    mp.set_property_native("mute", false)
    muted_by_us = false
  end
end

mp.add_periodic_timer(0.2, tick)

mp.register_event("start-file", function()
  disarm_all()
  armed_at = nil
end)
mp.register_event("playback-restart", function()
  if armed_at == nil then armed_at = mp.get_time() end
end)

-- ---- 快捷键 ------------------------------------------------------------
-- Enter：撤销刚刚的跳过。
-- 必须用 add_forced_key_binding：实测 mpv 的 keypress 只派发 input.conf 里的绑定，
-- 而 add_key_binding(nil, name, ...) 需要用户在 input.conf 显式绑定才生效；
-- forced 绑定则会无条件覆盖同名按键（含 mpv 内置的 ENTER=playlist-next），
-- 因此无论用户的 input.conf 怎么写，撤销键都一定可用。
mp.add_forced_key_binding("ENTER", "undo-skip", do_undo)

-- n：跳到下一个片段（手动跳过）
mp.add_key_binding(nil, "skip-next", function()
  local pos = mp.get_property_number("time-pos") or 0
  -- 若正处在某个片段内，跳到该片段末尾；否则跳到下一个片段开头
  for _, s in ipairs(segments) do
    if pos >= s.start and pos < s.finish then
      log(string.format("MANUAL skip %.3f -> %.3f (%s)", pos, s.finish, s.category))
      mp.commandv("seek", tostring(s.finish), "absolute+exact")
      if notify then mp.osd_message("已跳过 " .. s.category, 1.5) end
      return
    end
  end
  local i = find_next(pos)
  if i then
    mp.commandv("seek", tostring(segments[i].start), "absolute+exact")
    if notify then mp.osd_message("跳到下一片段", 1) end
  else
    if notify then mp.osd_message("已是最后一个片段", 1) end
  end
end)

mp.add_key_binding(nil, "toggle", function()
  if armed_at then
    armed_at = nil
    disarm_all()
    if notify then mp.osd_message("广告跳过：已关闭", 2) end
  else
    armed_at = mp.get_time()
    if notify then mp.osd_message("广告跳过：已开启", 2) end
  end
end)
'''


def ensure_lua_script(config_dir: Path) -> Path:
    """把内嵌 Lua 脚本写入配置目录（内容变化才重写），返回脚本路径。

    写入配置目录而非 mpv-portable/：后者被 .gitignore 排除且随发行包分发，
    不适合存放运行时可变的脚本。配置目录同时避免被 mpv 的 scripts/ 自动加载
    导致与 --script 重复加载（实测重复加载会执行两次）。
    """
    path = Path(config_dir) / SCRIPT_NAME
    try:
        if path.exists() and path.read_text(encoding="utf-8") == SKIP_SCRIPT_LUA:
            return path
        Path(config_dir).mkdir(parents=True, exist_ok=True)
        # newline="" + utf-8 无 BOM：Lua 的 load() 不接受 BOM
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(SKIP_SCRIPT_LUA)
    except OSError:
        pass
    return path
