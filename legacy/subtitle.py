#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
subtitle.py — B 站原生字幕（CC / AI 字幕）获取与 ASS 转换

与弹幕的区别（实测）：
  * 弹幕端点公开无鉴权；字幕必须走 api.bilibili.com/x/player/wbi/v2，且**需要登录态**
    （SESSDATA 过期时会话初始化就会失败，因此这里复用已建好的 session）。
  * 并非所有视频都有字幕：实测抽查 8 个视频仅 4 个有 `ai-zh`。
    没有时接口返回空列表，本模块静默跳过，绝不影响播放。
  * 字幕体是 JSON：{"body": [{"from": 0.8, "to": 4.64, "content": "..."}, ...]}
    与 yt-dlp 内部 json2srt 拿到的结构一致。

字幕与弹幕可同时显示：弹幕走主字幕轨，原生字幕走 --secondary-sid。
"""

from pathlib import Path

__all__ = [
    "DEFAULT_CONFIG",
    "ALIGNMENTS",
    "ALIGNMENT_LABELS",
    "load_config",
    "save_config",
    "fetch_subtitles",
    "pick_subtitle",
    "download_cues",
    "cues_to_ass",
    "ensure_subtitle_ass",
]

#: 字幕默认配置。
#: 注意与弹幕的默认值不同 —— 字幕字号更大、不透明、贴底居中，
#: 所以两者各自独立成文件，不能共用一份配置。
DEFAULT_CONFIG = {
    "enabled": True,
    "font_face": "Microsoft YaHei",
    "font_size_ratio": 0.045,     # 相对视频高度（弹幕只有 0.025）
    "opacity": 1.0,               # 字幕默认完全不透明
    "color": "#FFFFFF",
    "back_color": "#000000",
    "border_style": "box",        # box=不透明底框（任何画面都可读）；outline=描边
    "outline": 2.0,
    "alignment": "bottom-center", # 默认底部居中
    "position_ratio": 0.06,       # 距对应边的距离 = 高度 × 该比例
}

#: 九宫格对齐选项（值 → 中文标签），供 GUI 下拉框使用
ALIGNMENTS = {
    "bottom-left": "底部 左",
    "bottom-center": "底部 中",
    "bottom-right": "底部 右",
    "middle-left": "中部 左",
    "middle-center": "中部 中",
    "middle-right": "中部 右",
    "top-left": "顶部 左",
    "top-center": "顶部 中",
    "top-right": "顶部 右",
}

#: 标签 → 值（GUI 反向查表）
ALIGNMENT_LABELS = {v: k for k, v in ALIGNMENTS.items()}


_CONFIG_TEMPLATE = """\
# ==============================================================================
# BiliYTPlayer — 原生字幕（CC / AI 字幕）配置
#
# 建议用 GUI 的「字幕设置…」按钮修改。改完后播放下一个视频即生效。
# 注意：本文件只管 api.bilibili.com 提供的字幕；
#       UP 主压制在画面里的硬字幕无法通过任何配置调整。
# ==============================================================================

enabled = true

# 字体与字号（字号 = 视频高度 × 该比例）
font_face = Microsoft YaHei
font_size_ratio = 0.045

# 文字颜色 / 底框颜色（#RRGGBB）
color = #FFFFFF
back_color = #000000

# 不透明度 0..1
opacity = 1.0

# 边框样式：box = 不透明底框（推荐，任何画面都清晰）；outline = 描边
border_style = box
outline = 2.0

# 对齐位置（九宫格）：bottom-center 为底部居中
# 可选：bottom-left / bottom-center / bottom-right
#       middle-left / middle-center / middle-right
#       top-left / top-center / top-right
alignment = bottom-center

# 距对应边的距离 = 视频高度 × 该比例（0.06 ≈ 6%）
position_ratio = 0.06
"""


def _config_path(config_dir: Path) -> Path:
    return Path(config_dir) / "subtitle.conf"


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
    """读取 subtitle.conf；缺失则写模板并返回默认值。异常一律回退默认。"""
    cfg = dict(DEFAULT_CONFIG)
    path = _config_path(config_dir)
    if not path.exists():
        try:
            Path(config_dir).mkdir(parents=True, exist_ok=True)
            path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
            if log:
                log(f"[*] 已生成字幕配置模板: {path}")
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
            log(f"[!] 字幕配置读取失败，使用默认值: {e}")
        return dict(DEFAULT_CONFIG)

    # 校验枚举型字段
    if cfg.get("alignment") not in ALIGNMENTS:
        cfg["alignment"] = DEFAULT_CONFIG["alignment"]
    if str(cfg.get("border_style")) not in ("box", "outline"):
        cfg["border_style"] = DEFAULT_CONFIG["border_style"]
    return cfg


def save_config(config_dir: Path, cfg: dict) -> bool:
    """回写 subtitle.conf（保留模板注释结构）。"""
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

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

#: 语言优先级（越靠前越先选）
PREFERRED_LANGS = ("zh-Hans", "zh-CN", "ai-zh", "zh", "zh-Hant", "ai-en", "en")


def _ass_time(t: float) -> str:
    t = max(0.0, float(t))
    return f"{int(t // 3600)}:{int((t % 3600) // 60):02d}:{t % 60:05.2f}"


def _ass_color_from_hex(hex_color: str, alpha: str = "00") -> str:
    """#RRGGBB → ASS &HAABBGGRR&。非法值回退白色。"""
    s = str(hex_color or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        s = "FFFFFF"
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        r = g = b = 0xFF
    return f"&H{alpha}{b:02X}{g:02X}{r:02X}&"


def fetch_subtitles(session, bvid: str, cid, *, log=None) -> list:
    """列出某视频可用字幕。失败返回 []。

    返回元素形如 {"lan": "ai-zh", "lan_doc": "中文", "url": "https://..."}。
    """
    def _note(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass

    if session is None or not bvid or cid in (None, ""):
        return []
    try:
        resp = session.get(
            "https://api.bilibili.com/x/player/wbi/v2",
            params={"bvid": bvid, "cid": cid},
            headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com"},
            timeout=10,
        )
        j = resp.json()
    except Exception as e:
        _note(f"    [!] 字幕列表查询失败（已忽略）: {type(e).__name__}")
        return []

    if j.get("code") != 0:
        return []

    data = j.get("data") or {}
    if data.get("need_login_subtitle"):
        _note("    [*] 该视频字幕需要登录态，已跳过")
        return []

    out = []
    for s in (data.get("subtitle") or {}).get("subtitles") or []:
        url = s.get("subtitle_url") or ""
        if url.startswith("//"):
            url = "https:" + url
        if not url:
            continue
        out.append({"lan": s.get("lan") or "", "lan_doc": s.get("lan_doc") or "",
                    "url": url, "ai": s.get("ai_status") not in (None, 0)})
    return out


def pick_subtitle(subs, prefer=None) -> dict | None:
    """按语言优先级挑一条字幕；prefer 为指定的语言代码。"""
    if not subs:
        return None
    if prefer:
        for s in subs:
            if s.get("lan") == prefer:
                return s
    order = list(PREFERRED_LANGS) + [s.get("lan", "") for s in subs]
    for lang in order:
        for s in subs:
            if s.get("lan") == lang:
                return s
    return subs[0]


def download_cues(url: str, *, timeout: float = 10.0, log=None) -> list:
    """下载字幕 JSON，返回 [{start, end, text}]；失败返回 []。"""
    import json as _json
    import urllib.request

    def _note(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA, "Referer": "https://www.bilibili.com"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = _json.loads(r.read().decode("utf-8"))
    except Exception as e:
        _note(f"    [!] 字幕下载失败（已忽略）: {type(e).__name__}")
        return []

    out = []
    for cue in (j.get("body") or []):
        try:
            a = float(cue.get("from"))
            b = float(cue.get("to"))
        except (TypeError, ValueError):
            continue
        text = str(cue.get("content") or "").strip()
        if not text or b <= a:
            continue
        out.append({"start": a, "end": b, "text": text})
    return out


def cues_to_ass(cues, width: int = 1920, height: int = 1080,
                font_face: str = "Microsoft YaHei", cfg: dict = None) -> str:
    """字幕 → ASS。默认底部居中。

    位置由 ASS 的 Alignment + MarginV 决定：
      * Alignment 2 = 底部居中（3 右、1 左、5 中间居中、8 顶部居中）
      * MarginV 从底边量起，单位是 **PlayRes 像素**（即 width/height 坐标系）。
        正因为是像素，固定值在不同分辨率下观感差别很大 —— 早期实现写死 36px，
        在 4096x2048 上只有 1.76% 高度，字幕几乎贴着底边。
        现在按 height × position_ratio 计算，跨分辨率观感一致。
    """
    cfg = cfg or {}
    width = int(width) or 1920
    height = int(height) or 1080
    font_size = max(14, int(height * float(cfg.get("font_size_ratio", 0.045))))
    outline = float(cfg.get("outline", 2.0))
    border_style = 1 if str(cfg.get("border_style", "box")) == "outline" else 3

    # 位置：底部留白 = 画面高度 × position_ratio
    try:
        pos_ratio = float(cfg.get("position_ratio", 0.06))
    except (TypeError, ValueError):
        pos_ratio = 0.06
    pos_ratio = max(0.0, min(0.4, pos_ratio))
    margin_v = int(round(height * pos_ratio))

    # 对齐：默认底部居中(2)
    align = str(cfg.get("alignment", "bottom-center")).lower()
    an = {"bottom-left": 1, "bottom-center": 2, "bottom-right": 3,
          "middle-left": 4, "middle-center": 5, "middle-right": 6,
          "top-left": 7, "top-center": 8, "top-right": 9}.get(align, 2)
    # 顶部对齐时 MarginV 从顶边量起
    if an in (7, 8, 9):
        margin_v = int(round(height * max(0.01, pos_ratio)))

    try:
        o = max(0.0, min(1.0, float(cfg.get("opacity", 1.0))))
    except (TypeError, ValueError):
        o = 1.0
    alpha = f"{int(round((1.0 - o) * 255)):02X}"

    color = _ass_color_from_hex(cfg.get("color", "#FFFFFF"))
    back = _ass_color_from_hex(cfg.get("back_color", "#000000"), alpha="80")

    def esc(s: str) -> str:
        return (s.replace("\\", "\\\\").replace("{", "\\{")
                 .replace("}", "\\}").replace("\r", "").replace("\n", "\\N"))

    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
         "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
         "MarginL, MarginR, MarginV, Encoding"),
        (f"Style: CC,{font_face},{font_size},{color},{color},&H00000000,"
         f"{back},0,0,0,0,100,100,0,0,{border_style},{outline:g},0,{an},"
         f"40,40,{margin_v},1"),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    body = []
    for c in sorted(cues or [], key=lambda x: x["start"]):
        txt = esc(str(c.get("text") or ""))
        if not txt:
            continue
        eff = f"{{\\alpha&H{alpha}&}}" if alpha != "00" else ""
        body.append(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},"
                    f"CC,,0,0,0,,{eff}{txt}")
    return "\n".join(head + body) + "\n"


def ensure_subtitle_ass(session, bvid: str, cid, width: int, height: int, *,
                        config_dir: Path, prefer: str = None, cfg: dict = None,
                        force: bool = False, log=None) -> tuple:
    """完整链路：列字幕 → 选语言 → 下载 → 转 ASS → 落盘。

    返回 (ass_path 或 None, 语言代码 或 "")。
    """
    from danmaku import cache_dir_for

    cfg = cfg or {}
    cdir = cache_dir_for(config_dir)
    subs = fetch_subtitles(session, bvid, cid, log=log)
    if not subs:
        return None, ""
    chosen = pick_subtitle(subs, prefer)
    if not chosen:
        return None, ""

    lang = chosen.get("lan") or "sub"
    safe_lang = "".join(ch for ch in lang if ch.isalnum() or ch in "-_") or "sub"

    # 缓存 key 必须含样式指纹：否则用户改了位置/字号后，
    # 旧缓存仍被命中，设置看起来「没生效」。
    import hashlib
    style_keys = ("font_face", "font_size_ratio", "opacity", "color",
                  "back_color", "border_style", "outline",
                  "alignment", "position_ratio")
    fingerprint = hashlib.sha1(
        "|".join(f"{k}={cfg.get(k, DEFAULT_CONFIG.get(k))}" for k in style_keys).encode("utf-8")
    ).hexdigest()[:8]
    path = cdir / f"{bvid}_{int(width)}x{int(height)}.{safe_lang}.{fingerprint}.cc.ass"

    if not force and path.is_file() and path.stat().st_size > 0:
        try:
            path.touch()
        except OSError:
            pass
        return path, lang

    cues = download_cues(chosen["url"], log=log)
    if not cues:
        return None, ""
    ass = cues_to_ass(cues, width, height, cfg=cfg)
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(ass)
    except OSError as e:
        if log:
            log(f"    [!] 字幕写入失败（已忽略）: {e}")
        return None, ""
    return path, lang
