#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
danmaku.py — B 站弹幕获取与 ASS 转换

职责：把 B 站弹幕（XML）转成 mpv 可直接外挂显示的 ASS 字幕。

为什么不用 yt-dlp 插件：
  1. 本项目的 yt-dlp 是冻结版 exe，内嵌 Python 3.10，而 pip 装的 biliass 是
     cp311 编译扩展（_core.pyd），ABI 不匹配，插件永远 import 不进去；
  2. 冻结版只扫描 %APPDATA%\\yt-dlp\\plugins\\，不认 --plugin-dirs；
  3. biliass 为 GPLv3，静默引入有许可问题。
  而弹幕端点本身是公开无鉴权的，本项目又已经走 DASH 直链、手里就有 cid，
  因此整条链路用 stdlib 自实现，零新依赖。

两个真实踩过的坑（都有测试守着）：
  * 弹幕响应带 `Content-Encoding: deflate`，但用的是 **raw deflate**（无 zlib 头），
    必须 `zlib.decompress(raw, -15)`；直接 decode('utf-8') 会 UnicodeDecodeError。
  * 轨道分配若写成「轨道被占用后需等固定时长」，会丢掉约 91% 的弹幕。
    正确做法是 Danmaku2ASS 式：弹幕**尾部离开屏幕后轨道即可复用**，
    这样保留率可达 99.9%。
"""

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

__all__ = [
    "DEFAULT_CONFIG",
    "COMMENT_URL",
    "MAX_DANMAKU",
    "load_config",
    "save_config",
    "fetch_xml",
    "parse_xml",
    "to_ass",
    "ensure_ass",
    "download_and_convert",
    "cache_dir_for",
    "prune_cache",
    "count_events",
]


def count_events(ass_text: str) -> int:
    """统计 ASS 中的 Dialogue 条数（测试与日志用）。"""
    return sum(1 for line in (ass_text or "").splitlines()
               if line.startswith("Dialogue:"))

#: 弹幕 XML 端点（公开、无需鉴权）
COMMENT_URL = "https://comment.bilibili.com/{cid}.xml"

#: 单视频弹幕上限，防止异常响应撑爆内存/ASS
MAX_DANMAKU = 20000

#: B 站弹幕模式
MODE_SCROLL = (1, 2, 3)   # 滚动（右→左）
MODE_BOTTOM = 4           # 底部固定
MODE_TOP = 5              # 顶部固定
MODE_REVERSE = 6          # 逆向滚动
MODE_SPECIAL = (7, 8)     # 高级弹幕：内容是嵌套 JSON/XML，非纯文本，一律丢弃

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


DEFAULT_CONFIG = {
    "enabled": True,
    "opacity": 0.8,            # 文字不透明度 0..1
    "font_face": "Microsoft YaHei",
    "font_size_ratio": 0.05,   # 相对视频高度（1080p 下约 54px，观感接近 B 站默认）
    "duration_marquee": 12.0,  # 滚动弹幕存活秒数
    "duration_still": 5.0,     # 固定弹幕存活秒数
    "display_region": 0.80,    # 占用画面上方比例（下方留给字幕与 OSC）
    "block_top": False,        # 屏蔽顶部固定弹幕
    "block_bottom": False,     # 屏蔽底部固定弹幕
    "block_scroll": False,     # 屏蔽滚动弹幕
    "block_keywords": "",      # 逗号分隔关键词，命中即丢弃
    "speedup_gap": 0.2,        # 同轨最小间隔（秒）
    "outline": 2.0,            # 描边宽度（可读性）
    "scale_xml_size": True,    # 把 B 站的字号字段按 1080p 基准等比放大
    "cache_limit": 200,        # 缓存文件数上限
}


_CONFIG_TEMPLATE = """\
# ==============================================================================
# BiliYTPlayer — 弹幕配置
#
# 建议用 GUI 的「弹幕设置…」按钮修改，避免格式出错。
# 改完配置后，播放下一个视频即生效（无需重启）。
# ==============================================================================

# 弹幕总开关。false = 完全不下载弹幕
enabled = true

# 文字不透明度 0..1（1 = 完全不透明）
opacity = 0.8

# 字体
font_face = Microsoft YaHei

# 字号比例：字号 = 视频高度 × 该比例（0.05 ≈ 1080p 下 54px）
# 该比例在任何分辨率下观感一致，4K 视频会自动放大，不会变小
font_size_ratio = 0.05

# 滚动弹幕 / 固定弹幕 的存活秒数
duration_marquee = 12.0
duration_still = 5.0

# 弹幕占用画面上方比例，下方留白给字幕与进度条
# 注意：该值过小会显著减少可用轨道数，导致大量弹幕被丢弃（0.3 以下尤其明显）
display_region = 0.80

# 屏蔽某类弹幕
block_top = false
block_bottom = false
block_scroll = false

# 关键词屏蔽，逗号分隔（命中即丢弃该条弹幕）
block_keywords =

# 同轨最小间隔（秒），越大越不拥挤
speedup_gap = 0.2

# 描边宽度，越大越清晰但越粗
outline = 2.0

# 把 B 站的字号字段按 1080p 基准等比缩放（true 时 4K 视频字号自动放大）
# 关掉后直接使用 B 站原始像素值，4K 下会显得很小
scale_xml_size = true

# 缓存文件数上限（超出后按最久未用清理）
cache_limit = 200
"""


def cache_dir_for(config_dir: Path) -> Path:
    """弹幕 ASS 缓存目录。"""
    return Path(config_dir) / "cache"


def _config_path(config_dir: Path) -> Path:
    return Path(config_dir) / "danmaku.conf"


def _coerce(key: str, raw: str):
    default = DEFAULT_CONFIG[key]
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


def load_config(config_dir: Path, log=None) -> dict:
    """读取 danmaku.conf；缺失则写模板并返回默认值。异常一律回退默认。"""
    cfg = dict(DEFAULT_CONFIG)
    path = _config_path(config_dir)
    if not path.exists():
        try:
            Path(config_dir).mkdir(parents=True, exist_ok=True)
            path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
            if log:
                log(f"[*] 已生成弹幕配置模板: {path}")
        except OSError:
            pass
        return cfg
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";", "[")) or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k in DEFAULT_CONFIG:
                cfg[k] = _coerce(k, v)
    except (OSError, UnicodeDecodeError) as e:
        if log:
            log(f"[!] 弹幕配置读取失败，使用默认值: {e}")
        return dict(DEFAULT_CONFIG)
    return cfg


def save_config(config_dir: Path, cfg: dict) -> bool:
    """回写 danmaku.conf（保留模板注释结构）。"""
    path = _config_path(config_dir)
    try:
        Path(config_dir).mkdir(parents=True, exist_ok=True)
        lines = []
        for line in _CONFIG_TEMPLATE.splitlines():
            s = line.strip()
            if s.startswith("#") or not s:
                lines.append(line)
                continue
            k, _, _ = s.partition("=")
            k = k.strip()
            if k in cfg:
                v = cfg[k]
                if isinstance(v, bool):
                    v = "true" if v else "false"
                lines.append(f"{k} = {v}")
            else:
                lines.append(line)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


# ============================================================================
# 下载
# ============================================================================

def fetch_xml(cid, *, timeout: float = 8.0, log=None) -> bytes:
    """下载弹幕 XML，返回**已解压**的原始字节。

    任何异常都返回 b""，绝不抛出（弹幕失败不能影响播放）。
    """
    def _note(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass

    url = COMMENT_URL.format(cid=cid)
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Referer": "https://www.bilibili.com",
        "Accept": "application/xml,text/xml,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
    except (urllib.error.URLError, OSError, ValueError) as e:
        _note(f"    [!] 弹幕下载失败（已忽略）: {type(e).__name__}")
        return b""

    if not raw:
        return b""

    # 关键：B 站返回的是 raw deflate（无 zlib 头），必须用 -15
    if "deflate" in enc:
        try:
            raw = zlib.decompress(raw, -15)
        except zlib.error:
            try:
                raw = zlib.decompress(raw)        # 少数情况带 zlib 头
            except zlib.error:
                _note("    [!] 弹幕解压失败（已忽略）")
                return b""
    elif "gzip" in enc:
        try:
            import gzip
            raw = gzip.decompress(raw)
        except OSError:
            _note("    [!] 弹幕 gzip 解压失败（已忽略）")
            return b""

    # 未声明压缩但内容不是 XML（例如某些中间层）时，尝试补一次 raw deflate
    if not raw.lstrip().startswith(b"<"):
        try:
            cand = zlib.decompress(raw, -15)
            if cand.lstrip().startswith(b"<"):
                raw = cand
        except zlib.error:
            pass

    return raw


# ============================================================================
# 解析
# ============================================================================

def parse_xml(raw: bytes) -> list:
    """解析弹幕 XML → [{start, mode, size, color, text}, ...]。

    p 属性格式：时间, 模式, 字号, 颜色(十进制RGB), 时间戳, 池, uid, 弹幕id, ...
    """
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    out = []
    for d in root.iter("d"):
        p = (d.get("p") or "").split(",")
        if len(p) < 4:
            continue
        try:
            start = float(p[0])
            mode = int(p[1])
            size = int(p[2])
            color = int(p[3])
        except ValueError:
            continue

        # 高级弹幕（mode 7/8）是嵌套 JSON/XML，不是纯文本，直接丢弃
        if mode in MODE_SPECIAL:
            continue

        text = (d.text or "").strip()
        if not text:
            continue

        out.append({"start": start, "mode": mode, "size": size,
                    "color": color, "text": text})
        if len(out) >= MAX_DANMAKU:
            break
    return out


# ============================================================================
# 转换
# ============================================================================

def _ass_time(t: float) -> str:
    t = max(0.0, float(t))
    return f"{int(t // 3600)}:{int((t % 3600) // 60):02d}:{t % 60:05.2f}"


def _ass_color(dec: int) -> str:
    """B 站十进制 RGB → ASS &H00BBGGRR&（ASS 是 BGR 序）。"""
    try:
        dec = int(dec)
    except (TypeError, ValueError):
        dec = 0xFFFFFF
    r = (dec >> 16) & 0xFF
    g = (dec >> 8) & 0xFF
    b = dec & 0xFF
    return f"&H00{b:02X}{g:02X}{r:02X}&"


def _ass_alpha(opacity: float) -> str:
    """不透明度 0..1 → ASS alpha（00 不透明，FF 全透明）。"""
    try:
        o = max(0.0, min(1.0, float(opacity)))
    except (TypeError, ValueError):
        o = 0.8
    return f"{int(round((1.0 - o) * 255)):02X}"


def _escape_ass(text: str) -> str:
    """转义 ASS 特殊字符，避免弹幕内容破坏标签。"""
    return (text.replace("\\", "\\\\")
                .replace("{", "\\{")
                .replace("}", "\\}")
                .replace("\r", "")
                .replace("\n", " "))


def _text_width(text: str, size: int) -> float:
    """估算文本像素宽度：CJK 按 1.0 em，ASCII 按 0.5 em。"""
    units = 0.0
    for ch in text:
        units += 1.0 if ord(ch) > 0x2E7F else 0.5
    return max(1.0, units * size)


def _safe_start(x) -> float:
    """把任意 start 值转成可排序的 float（非法值当 0）。

    排序键必须绝对健壮：上游可能是 None、字符串甚至嵌套结构，
    只要有一项抛错，整个视频的弹幕就全没了。
    """
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _pick_lane(free, t0):
    """从最上面开始找第一条空闲轨道；都不空闲返回 None。

    这就是 B 站官方播放器的行为：弹幕**从顶部向下紧贴堆叠**，
    新弹幕优先占用最上面的空轨道，下方轨道只在必要时才被使用。

    历史教训：我曾改成「选空闲最久的轨道」以求各轨均匀分布，
    结果弹幕被均匀撒满整个区域，看起来稀疏、不像 B 站，
    且与 display_region 叠加后观感更差（用户实测反馈要改回来）。
    均匀分布并不是用户想要的 —— 还原官方观感才是。
    """
    for i, ft in enumerate(free):
        if ft <= t0:
            return i
    return None


def to_ass(items, width: int, height: int, cfg: dict = None) -> str:
    """把弹幕列表转成 ASS 文本。

    轨道分配采用 Danmaku2ASS 式策略：某条滚动弹幕的**尾部完全离开屏幕**后，
    它占用的轨道即可复用。这是高保留率的关键 —— 若改成「轨道固定占用 N 秒」，
    实测会丢掉约 91% 的弹幕。

    注意：**必须先按时间排序**。B 站返回的 XML 并非按时间递增
    （实测首条是 495.8s，第二条却是 50.4s）。若直接顺序分配轨道，
    轨道空闲时间会被乱序时间戳向后推，导致大量弹幕被判为「无轨道可用」——
    实测保留率只有 27.8%；排序后为 100%。
    """
    cfg = dict(DEFAULT_CONFIG, **(cfg or {}))
    width = int(width) or 1920
    height = int(height) or 1080

    # 关键：B 站 XML 乱序，必须自己排。
    # 先滤掉非 dict 项，排序键用 _safe_start，任何脏值都不会中断整批转换。
    items = [x for x in (items or []) if isinstance(x, dict)]
    items.sort(key=lambda x: _safe_start(x.get("start")))

    font_size = max(12, int(height * float(cfg.get("font_size_ratio", 0.028))))

    # B 站弹幕 XML 的 size 字段是**为 1920x1080 画布设计的绝对像素值**（常见 25），
    # 而 ASS 的 \\fs 单位是 PlayRes 像素。直接照搬会导致：
    #   1080p -> 25px = 2.3% 屏高（尚可）
    #   4K    -> 25px = 1.2% 屏高（小到看不清，用户实测反馈）
    # 因此改为「按 1080p 基准的相对比例」：
    #   最终字号 = 视频高度 × font_size_ratio × (该弹幕 size / 25)
    # 这样 font_size_ratio 在任何分辨率下都是同一个观感，
    # 而弹幕之间原有的相对大小差异（有人发大字号）依然保留。
    XML_BASE_SIZE = 25.0
    scale_xml = bool(cfg.get("scale_xml_size", True))
    max_ratio = float(cfg.get("max_size_ratio", 1.6))
    dur_m = max(1.0, float(cfg.get("duration_marquee", 12.0)))
    dur_s = max(1.0, float(cfg.get("duration_still", 5.0)))
    region = min(1.0, max(0.1, float(cfg.get("display_region", 0.85))))
    gap = max(0.0, float(cfg.get("speedup_gap", 0.2)))
    outline = float(cfg.get("outline", 2.0))
    alpha = _ass_alpha(cfg.get("opacity", 0.8))

    block_kw = [k.strip() for k in str(cfg.get("block_keywords") or "").split(",") if k.strip()]

    lane_h = max(1, int(font_size * 1.2))
    screen_h = int(height * region)
    n_lanes = max(1, screen_h // lane_h)

    # 固定弹幕只用上半部分的一部分轨道，避免与滚动弹幕抢满
    n_fixed = max(1, n_lanes // 2)

    lane_free = [0.0] * n_lanes       # 滚动
    top_free = [0.0] * n_fixed        # 顶部
    btm_free = [0.0] * n_fixed        # 底部

    events = []
    for it in items or []:
        mode = it.get("mode", 1)
        if mode in MODE_SCROLL and cfg.get("block_scroll"):
            continue
        if mode == MODE_TOP and cfg.get("block_top"):
            continue
        if mode == MODE_BOTTOM and cfg.get("block_bottom"):
            continue

        text = str(it.get("text") or "")
        if not text:
            continue
        if block_kw and any(k in text for k in block_kw):
            continue

        try:
            t0 = max(0.0, float(it.get("start")))
        except (TypeError, ValueError):
            continue
        # 字号：以 font_size_ratio 为基准，再乘以该弹幕原有的相对大小。
        # font_size_ratio 在任何分辨率下观感一致（这是修 4K 弹幕过小的关键）。
        try:
            raw_size = float(it.get("size") or 0)
        except (TypeError, ValueError):
            raw_size = 0.0
        if scale_xml and raw_size > 0:
            ratio = raw_size / XML_BASE_SIZE
        else:
            ratio = 1.0
        ratio = max(0.5, min(ratio, max_ratio))
        size = max(12, int(round(font_size * ratio)))
        color = _ass_color(it.get("color", 0xFFFFFF))
        esc = _escape_ass(text)

        if mode in MODE_SCROLL or mode == MODE_REVERSE:
            w = _text_width(text, size)
            speed = (width + w) / dur_m              # px/s
            # 尾部完全离开屏幕所需时间
            clear = w / speed
            lane = _pick_lane(lane_free, t0)
            if lane is not None:
                lane_free[lane] = t0 + clear + gap
                y = int(lane * lane_h + lane_h)
                if mode == MODE_REVERSE:
                    x_from, x_to = -int(w), int(width + w)
                else:
                    x_from, x_to = int(width + w), -int(w)
                events.append((t0, t0 + dur_m,
                               f"{{\\move({x_from},{y},{x_to},{y})"
                               f"\\c{color}\\alpha&H{alpha}&\\fs{size}"
                               f"\\bord{outline:g}}}", esc))
        elif mode == MODE_TOP:
            lane = _pick_lane(top_free, t0)
            if lane is not None:
                top_free[lane] = t0 + dur_s
                y = int(lane * lane_h + lane_h)
                events.append((t0, t0 + dur_s,
                               f"{{\\an8\\pos({width // 2},{y})"
                               f"\\c{color}\\alpha&H{alpha}&\\fs{size}"
                               f"\\bord{outline:g}}}", esc))
        elif mode == MODE_BOTTOM:
            lane = _pick_lane(btm_free, t0)
            if lane is not None:
                btm_free[lane] = t0 + dur_s
                y = int(screen_h - lane * lane_h - lane_h)
                events.append((t0, t0 + dur_s,
                               f"{{\\an2\\pos({width // 2},{y})"
                               f"\\c{color}\\alpha&H{alpha}&\\fs{size}"
                               f"\\bord{outline:g}}}", esc))
        else:
            # 未知模式（mode 6 之外的扩展值）：忽略
            continue

    events.sort(key=lambda e: e[0])

    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
         "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
         "MarginL, MarginR, MarginV, Encoding"),
        (f"Style: DM,{cfg.get('font_face', 'Microsoft YaHei')},{font_size},"
         f"&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,"
         f"{outline:g},1,7,0,0,0,1"),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    body = [f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},DM,,0,0,0,,{eff}{txt}"
            for a, b, eff, txt in events]
    return "\n".join(head + body) + "\n"


# ============================================================================
# 落盘与缓存
# ============================================================================

def _style_fingerprint(cfg: dict, width: int, height: int) -> str:
    """样式指纹：样式一变，缓存文件名就变。

    真实 bug：原先缓存文件名只有 `<cid>_<W>x<H>.danmaku.ass`，
    不带任何样式信息。用户在 GUI 改了字号/位置后，旧缓存仍被命中，
    设置看起来「完全没生效」（用户实测反馈）。
    """
    import hashlib
    keys = ("font_face", "font_size_ratio", "opacity", "display_region",
            "duration_marquee", "duration_still", "outline", "speedup_gap",
            "block_top", "block_bottom", "block_scroll", "block_keywords",
            "scale_xml_size", "max_size_ratio")
    raw = "|".join(f"{k}={cfg.get(k, DEFAULT_CONFIG.get(k))}" for k in keys)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def ensure_ass(cid, width: int, height: int, *, config_dir: Path, cfg: dict = None,
               items=None, force: bool = False, log=None) -> Path | None:
    """生成（或复用缓存）某视频的弹幕 ASS，返回路径；失败返回 None。

    items 为 None 时自动下载并解析。缓存 key 含分辨率与**样式指纹**，
    保证改样式后一定重新生成。
    """
    cfg = dict(DEFAULT_CONFIG, **(cfg or {}))
    cdir = cache_dir_for(config_dir)
    fp = _style_fingerprint(cfg, width, height)
    path = cdir / f"{cid}_{int(width)}x{int(height)}.{fp}.danmaku.ass"

    if not force and path.is_file() and path.stat().st_size > 0:
        try:
            Path(path).touch()          # 更新 mtime，供 LRU 清理
        except OSError:
            pass
        return path

    if items is None:
        raw = fetch_xml(cid, log=log)
        if not raw:
            return None
        items = parse_xml(raw)
    if not items:
        if log:
            log("    [*] 该视频无弹幕")
        return None

    ass = to_ass(items, width, height, cfg)
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        # UTF-8 无 BOM：libass 不接受 BOM
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(ass)
    except OSError as e:
        if log:
            log(f"    [!] 弹幕写入失败（已忽略）: {e}")
        return None

    # 可用轨道太少会让大量弹幕被丢弃（实测 2 条轨道时保留率仅 21%），
    # 观感也会变成「全挤在顶部两行」。提示用户去调字号/显示区域。
    if log:
        try:
            fs = max(12, int(int(height) * float(cfg.get("font_size_ratio", 0.05))))
            lane_h = max(1, int(fs * 1.2))
            n_lanes = max(1, int(int(height) * float(cfg.get("display_region", 0.80))) // lane_h)
            emitted = count_events(ass)
            if n_lanes < 4 or (items and emitted < len(items) * 0.6):
                log(f"    [!] 弹幕轨道偏少（{n_lanes} 条），"
                    f"{len(items) - emitted}/{len(items)} 条被丢弃；"
                    f"可调小字号比例或调大显示区域")
        except Exception:
            pass
    return path


def download_and_convert(cid, width: int, height: int, *, config_dir: Path,
                         cfg: dict = None, log=None) -> tuple:
    """完整链路：下载 → 解析 → 转换 → 落盘。

    返回 (ass_path 或 None, 弹幕条数)。
    """
    cfg = cfg or DEFAULT_CONFIG
    raw = fetch_xml(cid, log=log)
    if not raw:
        return None, 0
    items = parse_xml(raw)
    if not items:
        return None, 0
    path = ensure_ass(cid, width, height, config_dir=config_dir, cfg=cfg,
                      items=items, log=log)
    return path, len(items)


def prune_cache(config_dir: Path, limit: int = 200) -> int:
    """按最久未使用清理缓存，返回删除的文件数。"""
    cdir = cache_dir_for(config_dir)
    if not cdir.is_dir():
        return 0
    try:
        files = [p for p in cdir.iterdir() if p.is_file()]
    except OSError:
        return 0
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 200
    if len(files) <= limit:
        return 0
    try:
        files.sort(key=lambda p: p.stat().st_mtime)
    except OSError:
        return 0
    removed = 0
    for p in files[:len(files) - limit]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed
