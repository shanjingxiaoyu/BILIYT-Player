# -*- coding: utf-8 -*-
"""设置页桥 —— 把 4 份 conf / general.conf / .env 的读写暴露给 QML。

QML 侧通过上下文属性 ``settingsBridge`` 访问（见 ui-kit/CONTRACT.md）。

两条硬规则：

1. **范围钳制全部在这里做**。QML 的 ``DoubleValidator`` 只是软约束（用户能粘贴
   超范围的值），数字与 legacy/bili_yt_player.pyw 第 439-478 / 547-570 / 689-712 行
   逐字对应。
2. ``app/autostart.py`` / ``app/quality.py`` 由并行子智能体编写，**导入失败一律降级**
   （兜底选项 + 明确的提示文字），不让设置页整体不可用。
"""

from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

ROOT = Path(__file__).resolve().parent.parent
_LEGACY = ROOT / "legacy"
for _p in (str(ROOT), str(_LEGACY)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ============================================================================
# 小工具
# ============================================================================

def _mod(name: str):
    """导入 legacy/app 模块（sys.path 已在模块顶部补齐）。"""
    return importlib.import_module(name)


def _num(v, default: float = 0.0) -> float:
    """宽松转 float —— 与 legacy 的 ``_num(var, default)`` 等价。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _opt_num(v):
    """转 float，失败返回 None（legacy 里 ``try/except ValueError: pass`` 的语义）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(v, lo: float, hi: float, default: float) -> float:
    return max(lo, min(hi, _num(v, default)))


def _text(v, default: str = "") -> str:
    s = str(v if v is not None else "").strip()
    return s or default


def _as_dict(data) -> dict:
    """QML 传进来的 ``{}`` 到 Python 侧是 QJSValue，不是 dict —— 必须转。

    （不转的话 ``isinstance(data, dict)`` 直接 False，所有字段被静默丢弃，
    表现为「点保存没反应」，而且不报错。）
    """
    if isinstance(data, dict):
        return data
    to_variant = getattr(data, "toVariant", None)
    if callable(to_variant):
        try:
            val = to_variant()
        except Exception:
            return {}
        return val if isinstance(val, dict) else {}
    return {}


def _ass_color_to_hex(ass: str) -> str:
    """ASS 的 ``&H00BBGGRR&`` → ``#RRGGBB``（与 legacy 的色块反解一致）。"""
    try:
        bb, gg, rr = ass[4:6], ass[6:8], ass[8:10]
        int(rr + gg + bb, 16)
        return f"#{rr}{gg}{bb}"
    except (ValueError, IndexError):
        return "#808080"


#: app/quality.py 尚未就绪时的兜底（内容与 CONTRACT.md 的 QUALITY_OPTIONS 一致）
_FALLBACK_QUALITY = (
    ("auto", "自动（最高可用）"), ("127", "8K"), ("126", "杜比视界"),
    ("125", "HDR"), ("120", "4K"), ("116", "1080P60"), ("112", "1080P+"),
    ("80", "1080P"), ("64", "720P"), ("32", "480P"), ("16", "360P"),
)

#: app/autostart.py 尚未就绪时的兜底（标签与 shell-agent 的实现保持一致）
_FALLBACK_AUTOSTART = (("none", "禁止自启"), ("registry", "开启自启"))

#: 自启模式 → 设置页显示文字。覆盖 app/autostart.py 的 "仅手动启动 / 开机自启"：
#: 区块标题已经是「启动设置」、行标签是「自动启动」，这里再写「开机自启」
#: 会变成三重重复，且「仅手动启动」对普通用户不够直白。
_AUTOSTART_LABELS = {"none": "禁止自启", "registry": "开启自启"}

#: app/theme.py 尚未就绪时的兜底
_FALLBACK_THEME = (("dark", "深色模式"), ("light", "浅色模式"), ("system", "跟随系统"))

#: SponsorBlock 四档的中文标签（与 legacy 的 tier_labels 一致）
_TIER_LABELS = {
    "auto": "自动跳过",
    "manual": "手动跳过",
    "bar": "仅在进度条显示",
    "prohibited": "禁用",
}


# ============================================================================
# Bridge
# ============================================================================

class SettingsBridge(QObject):
    """设置窗口的 Python 后端。所有方法返回 ``{"ok": bool, "msg": str}``。"""

    configChanged = Signal()          # 任何配置被保存后 emit（Lead 收到后重载 4 份 cfg）
    requestClose = Signal()           # 设置窗口请求关闭（QML 侧直接 close() 即可，保留接口）
    sessionStatus = Signal(bool, str)  # SESSDATA 校验结果（成功, 说明）
    themeChanged = Signal(str)        # 主题被保存并应用后 emit（规范化后的 light/dark/system）

    def __init__(self, config_dir: Path, log=None) -> None:
        super().__init__()
        self.config_dir = Path(config_dir)
        self.log = log or (lambda _m: None)
        # 可注入的 SESSDATA 校验器；自检脚本用它绕开网络
        self._validator = None

    # ---------------------------------------------------------------- 读 ----

    @Slot(result="QVariant")
    def snapshot(self):
        """一次取全量：4 个页签的数据 + 下拉选项。窗口打开/切页时调用。"""
        return {name: self._section(name) for name in
                ("general", "danmaku", "subtitle", "sponsor", "account", "meta")}

    @Slot(str, result="QVariant")
    def pageData(self, name: str):
        """单个页签的数据（切页时调用，避免每次读全部 conf）。"""
        try:
            return self._section(str(name))
        except Exception as e:
            self.log(f"[!] 设置页数据读取失败({name}): {e}")
            return {}

    def _section(self, name: str):
        return getattr(self, "_" + name)()

    def _general(self) -> dict:
        cfg = _mod("app.app_config").load_config(self.config_dir, self.log)
        # 附带注册表的真实状态：配置说 registry 但项被外部删掉时，UI 能提示出来
        cfg["autostart_enabled"] = self._autostart_enabled(cfg.get("autostart_mode", "none"))
        # 源码运行时 current_exe() 为 None，写注册表没有意义 → UI 显示
        # 「打包为 exe 后可开启」而不是干巴巴的「已关闭」
        cfg["autostart_available"] = self._autostart_available()
        # theme 已由 app_config 归一化（走 app/theme.normalize），这里再兜一次：
        # app_config 缺失该键时（老版本 conf）也得给 UI 一个合法值
        cfg["theme"] = self._normalize_theme(cfg.get("theme"))
        return cfg

    def _danmaku(self) -> dict:
        return _mod("danmaku").load_config(self.config_dir, self.log)

    def _subtitle(self) -> dict:
        return _mod("subtitle").load_config(self.config_dir, self.log)

    def _sponsor(self) -> dict:
        sb = _mod("sponsorblock")
        cfg = sb.load_config(self.config_dir, self.log)
        tiers = cfg.get("category_tiers") or dict(sb.CATEGORY_TIERS)
        cats = [{
            "key": cat,
            "name": sb.CATEGORY_NAMES.get(cat, cat),
            "desc": sb.CATEGORY_DESCS.get(cat, ""),
            "color": _ass_color_to_hex(sb.CATEGORY_COLORS.get(cat, "&H00D400&")),
            "tier": tiers.get(cat, "auto"),
        } for cat in sb.ALL_CATEGORIES]
        return {
            "categories": cats,
            "notify": bool(cfg.get("notify", True)),
            "undo_seconds": _num(cfg.get("undo_seconds"), 3.0),
            "min_duration": _num(cfg.get("min_duration"), 1.0),
        }

    def _account(self) -> dict:
        """只回显脱敏信息：前 4 位 + 总长度。"""
        sd = self._read_sessdata()
        if not sd:
            return {"configured": False, "masked": "未配置"}
        head = sd[:4] if len(sd) >= 4 else sd
        return {"configured": True, "masked": f"{head}****（共 {len(sd)} 字符）"}

    def _meta(self) -> dict:
        sub = _mod("subtitle")
        sb = _mod("sponsorblock")
        return {
            "alignments": [{"key": k, "label": v} for k, v in sub.ALIGNMENTS.items()],
            "tiers": [{"key": t, "label": _TIER_LABELS.get(t, t)} for t in sb.TIERS],
            "quality": [{"key": k, "label": v} for k, v in self._quality_options()],
            "autostart": [{"key": k, "label": v} for k, v in self._autostart_options()],
            "theme": [{"key": k, "label": v} for k, v in self._theme_options()],
        }

    def _theme_options(self) -> tuple:
        try:
            return tuple(_mod("app.theme").THEME_OPTIONS)
        except Exception:
            return _FALLBACK_THEME

    def _normalize_theme(self, name) -> str:
        try:
            return _mod("app.theme").normalize(name)
        except Exception:
            return name if name in ("light", "dark") else "system"

    # ------------------------------------------------------------ 通用页 ----

    @Slot("QVariant", result="QVariant")
    def saveGeneral(self, data):
        d = _as_dict(data)
        ac = _mod("app.app_config")
        cfg = ac.load_config(self.config_dir, self.log)

        if "close_action" in d:
            action = str(d.get("close_action") or cfg.get("close_action", "close"))
            # ask 是老默认值；UI 只给 close / tray 两个单选，但保留 ask 合法
            if action in ("close", "tray", "ask"):
                cfg["close_action"] = action
        if "ask_on_close" in d:
            cfg["ask_on_close"] = bool(d.get("ask_on_close"))
        if "quality" in d:
            quality = str(d.get("quality") or "")
            if quality in {k for k, _ in self._quality_options()}:
                cfg["quality"] = quality

        # 主题：归一化 → 立即应用 → 记下是否变了（变了才 emit，避免主窗口无谓刷新）
        theme_before = cfg.get("theme", "system")
        if "theme" in d:
            cfg["theme"] = self._normalize_theme(d.get("theme"))
            try:
                _mod("app.theme").apply_theme(cfg["theme"])
            except Exception as e:
                self.log(f"[!] 主题应用失败（设置已保存，重启后生效）: {e}")
        theme_after = cfg.get("theme", "system")

        extra = ""
        if "autostart_mode" in d:
            mode = str(d.get("autostart_mode") or "none")
            # 每次都写一遍注册表：值没变也无害，还能顺带修复「配置说 registry
            # 但注册表项被外部删掉」的不一致状态。
            ok_auto, note = self._apply_autostart(mode)
            if ok_auto:
                cfg["autostart_mode"] = mode
            else:
                cfg["autostart_mode"] = "none"
                extra = f"；{note}" if note else ""

        if not ac.save_config(self.config_dir, cfg):
            return {"ok": False, "msg": "保存失败，请检查文件权限"}
        self.configChanged.emit()
        if theme_after != theme_before:
            self.themeChanged.emit(theme_after)
        return {"ok": True, "msg": "已保存" + extra}

    # ------------------------------------------------------------ 画质页 ----

    def _quality_options(self) -> tuple:
        try:
            return tuple(_mod("app.quality").QUALITY_OPTIONS)
        except Exception:
            return _FALLBACK_QUALITY

    # -------------------------------------------------------- 开机自启 ----

    def _autostart_options(self) -> tuple:
        """自启下拉选项。

        取值来自 app/autostart.py 的 options()（shell-agent 的文件，不改它）；
        **显示文字在这里覆盖**成用户要求的说法（「禁止自启 / 开启自启」），
        避免设置页里「开机自启 → 启动方式 → 开机自启」的三重重复。
        """
        try:
            raw = tuple(_mod("app.autostart").options()) or _FALLBACK_AUTOSTART
        except Exception:
            raw = _FALLBACK_AUTOSTART
        return tuple((key, _AUTOSTART_LABELS.get(key, label)) for key, label in raw)

    def _apply_autostart(self, mode: str) -> tuple:
        """调 app/autostart.py 的 apply()；模块没就绪时返回 (False, 可读原因)。"""
        try:
            auto = _mod("app.autostart")
        except Exception as e:
            self.log(f"[!] 开机自启模块不可用: {e}")
            return False, "开机自启模块不可用（app/autostart.py 未就绪），已保留原设置"
        try:
            ok, note = auto.apply(mode, auto.current_exe())
            return bool(ok), str(note or "")
        except Exception as e:
            self.log(f"[!] 开机自启写入失败: {e}")
            return False, f"开机自启设置失败：{e}"

    def _autostart_enabled(self, mode: str) -> bool:
        try:
            return bool(_mod("app.autostart").is_enabled(mode))
        except Exception:
            return False

    def _autostart_available(self) -> bool:
        """能否真正写自启：源码运行时 current_exe() 为 None，只能打包后用。"""
        try:
            return bool(_mod("app.autostart").current_exe())
        except Exception:
            return False

    # ------------------------------------------------------------ 弹幕页 ----

    @Slot("QVariant", result="QVariant")
    def saveDanmaku(self, data):
        d = _as_dict(data)
        dm = _mod("danmaku")
        cfg = dm.load_config(self.config_dir, self.log)

        cfg["opacity"] = _clamp(d.get("opacity"), 0.05, 1.0, 0.7)
        # 上限 0.20 而不是 0.08：原先 min(0.08, ...) 会把用户输入压回 0.08，
        # 看起来像「设置不生效」（用户实测反馈，见 legacy/bili_yt_player.pyw:550）
        cfg["font_size_ratio"] = _clamp(d.get("font_size_ratio"), 0.01, 0.20, 0.04)
        cfg["duration_marquee"] = _clamp(d.get("duration_marquee"), 2.0, 30.0, 12.0)
        cfg["duration_still"] = _clamp(d.get("duration_still"), 1.0, 20.0, 5.0)
        # 显示区域至少 0.3，否则轨道太少会导致大量弹幕被丢弃
        cfg["display_region"] = _clamp(d.get("display_region"), 0.3, 1.0, 0.30)
        cfg["outline"] = _clamp(d.get("outline"), 0.0, 6.0, 2.0)
        cfg["font_face"] = _text(d.get("font_face"), "Microsoft YaHei")
        cfg["block_keywords"] = _text(d.get("block_keywords"), "")
        cfg["block_top"] = bool(d.get("block_top", False))
        cfg["block_bottom"] = bool(d.get("block_bottom", False))
        cfg["block_scroll"] = bool(d.get("block_scroll", False))

        if not dm.save_config(self.config_dir, cfg):
            return {"ok": False, "msg": "保存失败，请检查文件权限"}
        self.configChanged.emit()
        return {"ok": True, "msg": "已保存，播放下一个视频即生效"}

    @Slot(result="QVariant")
    def clearDanmakuCache(self):
        try:
            dm = _mod("danmaku")
            cdir = dm.cache_dir_for(self.config_dir)
            n = 0
            if cdir.is_dir():
                for p in cdir.iterdir():
                    if p.is_file():
                        p.unlink()
                        n += 1
            return {"ok": True, "msg": f"已清空弹幕缓存：{n} 个文件"}
        except Exception as e:
            return {"ok": False, "msg": f"清理失败：{e}"}

    # ------------------------------------------------------------ 字幕页 ----

    @Slot("QVariant", result="QVariant")
    def saveSubtitle(self, data):
        d = _as_dict(data)
        sub = _mod("subtitle")
        cfg = sub.load_config(self.config_dir, self.log)

        align = str(d.get("alignment") or cfg.get("alignment", "bottom-center"))
        cfg["alignment"] = align if align in sub.ALIGNMENTS else "bottom-center"
        cfg["position_ratio"] = _clamp(d.get("position_ratio"), 0.0, 0.4, 0.06)
        cfg["font_size_ratio"] = _clamp(d.get("font_size_ratio"), 0.01, 0.12, 0.045)
        cfg["font_face"] = _text(d.get("font_face"), "Microsoft YaHei")
        cfg["color"] = _text(d.get("color"), "#FFFFFF")
        cfg["back_color"] = _text(d.get("back_color"), "#000000")
        cfg["opacity"] = _clamp(d.get("opacity"), 0.05, 1.0, 1.0)
        cfg["outline"] = _clamp(d.get("outline"), 0.0, 6.0, 2.0)
        border = str(d.get("border_style") or cfg.get("border_style", "box"))
        cfg["border_style"] = border if border in ("box", "outline") else "box"

        if not sub.save_config(self.config_dir, cfg):
            return {"ok": False, "msg": "保存失败，请检查文件权限"}
        self.configChanged.emit()
        name = sub.ALIGNMENTS.get(cfg["alignment"], cfg["alignment"])
        return {"ok": True, "msg": f"已保存：{name}，距边 {cfg['position_ratio']:.0%}"}

    @Slot(result="QVariant")
    def subtitleDefaults(self):
        """「恢复默认」用的字段值（对齐 legacy：只回填，不落盘）。"""
        d = _mod("subtitle").DEFAULT_CONFIG
        return {
            "alignment": d.get("alignment", "bottom-center"),
            "position_ratio": d.get("position_ratio", 0.06),
            "font_size_ratio": d.get("font_size_ratio", 0.045),
            "font_face": d.get("font_face", "Microsoft YaHei"),
            "color": d.get("color", "#FFFFFF"),
            "back_color": d.get("back_color", "#000000"),
            "opacity": d.get("opacity", 1.0),
            "outline": d.get("outline", 2.0),
            "border_style": d.get("border_style", "box"),
        }

    # ---------------------------------------------------------- 广告跳过 ----

    @Slot("QVariant", result="QVariant")
    def saveSponsor(self, data):
        d = _as_dict(data)
        sb = _mod("sponsorblock")
        cfg = sb.load_config(self.config_dir, self.log)

        tiers = {}
        for item in (d.get("categories") or []):
            if not isinstance(item, dict):
                continue
            cat = str(item.get("key") or "")
            tier = str(item.get("tier") or "")
            if cat in sb.ALL_CATEGORIES and tier in sb.TIERS:
                tiers[cat] = tier
        cfg["category_tiers"] = tiers or dict(sb.CATEGORY_TIERS)

        if "notify" in d:
            cfg["notify"] = bool(d.get("notify"))
        undo = _opt_num(d.get("undo_seconds"))
        if undo is not None:
            cfg["undo_seconds"] = max(0.0, undo)
        shortest = _opt_num(d.get("min_duration"))
        if shortest is not None:
            cfg["min_duration"] = max(0.0, shortest)

        if not sb.save_config(self.config_dir, cfg):
            return {"ok": False, "msg": "保存失败，请检查文件权限"}
        self.configChanged.emit()
        auto_n = sum(1 for t in cfg["category_tiers"].values() if t == "auto")
        manual_n = sum(1 for t in cfg["category_tiers"].values() if t == "manual")
        return {"ok": True, "msg": f"已保存：自动跳过 {auto_n} 类，手动跳过 {manual_n} 类"}

    # ---------------------------------------------------------- B站账号 ----

    def _env_path(self) -> Path:
        return self.config_dir / ".env"

    def _read_sessdata(self) -> str:
        path = self._env_path()
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                key, _, val = line.partition("=")
                if key.strip() == "SESSDATA":
                    return val.strip().strip("\"'")
        except (OSError, UnicodeDecodeError):
            pass
        return ""

    def _check_sessdata(self, sd: str) -> bool:
        if self._validator is not None:
            return bool(self._validator(sd))
        return bool(_mod("bili_clipboard_dolby").validate_sessdata(sd))

    @Slot(str, result=bool)
    def verifySessdata(self, sessdata: str) -> bool:
        """校验并写 .env。校验有网络请求，放到后台线程，结果走 sessionStatus。"""
        sd = _text(sessdata, "").strip("\"'")
        if not sd:
            self.sessionStatus.emit(False, "请输入 SESSDATA")
            return False
        threading.Thread(target=self._verify_worker, args=(sd,), daemon=True).start()
        return True

    def _verify_worker(self, sd: str) -> None:
        try:
            ok = self._check_sessdata(sd)
        except Exception as e:
            self.sessionStatus.emit(False, f"校验失败：{e}")
            return
        if not ok:
            self.sessionStatus.emit(False, "SESSDATA 无效或已过期（检查是否复制完整）")
            return
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            # 格式与 legacy/bili_yt_player.pyw:877 完全一致
            self._env_path().write_text(
                "# B 站登录态 Cookie\nSESSDATA=" + sd + "\n", encoding="utf-8")
        except OSError as e:
            self.sessionStatus.emit(False, f"写入 .env 失败：{e}")
            return
        self.sessionStatus.emit(True, "已保存并验证通过")
        self.configChanged.emit()

    @Slot(result="QVariant")
    def reloadAccount(self):
        return self._account()


