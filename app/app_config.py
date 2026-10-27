# -*- coding: utf-8 -*-
"""通用设置（general.conf）读写 —— 与原 4 份 conf 完全同一套风格：`k = v` 纯文本。

字段严格限定在 DEFAULT_CONFIG 内，未知键忽略、脏值回退默认，
与 danmaku.py / sponsorblock.py 的 load_config 行为保持一致。
"""

from pathlib import Path

DEFAULT_CONFIG = {
    # 点 X 之后的行为：ask = 每次都问，close = 直接退出，tray = 最小化到托盘
    "close_action": "ask",
    # 关闭时是否弹「关闭程序 / 最小化到托盘」询问框（默认弹）
    "ask_on_close": True,
    # 开机自启方式，见 app/autostart.py 的 options()
    "autostart_mode": "none",
    # B 站画质偏好，见 app/quality.py 的 QUALITY_OPTIONS
    "quality": "auto",
    # 深浅色：system = 跟随系统；light = 强制浅色；dark = 强制深色
    # 合法值与归一化统一由 app/theme.py 负责（THEME_OPTIONS / normalize），
    # 这里只原样存取字符串，避免两处各定义一份取值表。
    "theme": "system",
}

_CONFIG_TEMPLATE = """\
# ==============================================================================
# BiliYTPlayer — 通用设置
#
# 建议用 GUI 的「设置」页面修改，避免格式出错。
# ==============================================================================

# 点窗口 X 之后的行为：ask = 每次都问；close = 直接退出；tray = 最小化到托盘
close_action = ask

# 关闭时是否弹询问框（false = 不再询问，直接执行 close_action）
ask_on_close = true

# 开机自启：none = 不自启；registry = 写入当前用户注册表 Run 项
autostart_mode = none

# B 站画质偏好：auto = 最高可用，其余为上限（80 = 1080P，120 = 4K，127 = 8K）
quality = auto

# 深浅色：system = 跟随系统；light = 浅色；dark = 深色
theme = system
"""


def _config_path(config_dir: Path) -> Path:
    return Path(config_dir) / "general.conf"


def _mod_theme():
    """延迟导入 app.theme：它是可选的（没有 GUI 时不该拖累配置读写）。"""
    from app import theme
    return theme


def _coerce(key: str, raw: str):
    key = key.strip()
    raw = raw.strip()
    if key in ("ask_on_close",):
        return raw.lower() in ("1", "true", "yes", "on")
    return raw


def load_config(config_dir: Path, log=None) -> dict:
    """读取 general.conf；缺失则写模板并返回默认值。异常一律回退默认。"""
    cfg = dict(DEFAULT_CONFIG)
    path = _config_path(config_dir)
    if not path.exists():
        try:
            Path(config_dir).mkdir(parents=True, exist_ok=True)
            path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
            if log:
                log(f"[*] 已生成通用配置模板: {path}")
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
            if k == "theme":
                # 归一化交给 app/theme.py（含别名容错），读不到就保留默认
                try:
                    cfg["theme"] = _mod_theme().normalize(v)
                except Exception:
                    cfg["theme"] = DEFAULT_CONFIG["theme"]
            elif k in DEFAULT_CONFIG:
                cfg[k] = _coerce(k, v)
    except (OSError, UnicodeDecodeError) as e:
        if log:
            log(f"[!] 通用配置读取失败，使用默认值: {e}")
        return dict(DEFAULT_CONFIG)
    return cfg


def save_config(config_dir: Path, cfg: dict) -> bool:
    """回写 general.conf（保留模板注释结构）。"""
    path = _config_path(config_dir)
    try:
        Path(config_dir).mkdir(parents=True, exist_ok=True)
        lines = []
        for line in _CONFIG_TEMPLATE.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                lines.append(line)
                continue
            k = s.split("=", 1)[0].strip()
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
