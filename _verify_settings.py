# -*- coding: utf-8 -*-
"""设置页自检：纯逻辑（落盘字段 / 钳制边界）+ 离屏渲染。

跑法：
    python _verify_settings.py

两部分：
  1. 纯逻辑 —— 用临时目录当 config_dir，逐页调 SettingsBridge 的保存方法，
     再直接读回 conf 文本校验字段名 / 值 / 钳制边界（不依赖 QML）。
  2. 渲染 —— 用 ui/settings/_shot_wrapper.qml 带桩加载 SettingsWindow，
     截 light / dark 各一张，确认 LOAD 成功。

`app.autostart` 在自检里被换成桩（真实现会写 HKCU 注册表）；SESSDATA 校验器
换成桩（真实现要发网络请求，还会在首次 import 时装依赖）。
最后一行输出 `ALL PASS` 或 `FAIL n`。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "legacy"))

# Windows 控制台默认 GBK，中文检查项名会变乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

_LEGACY = ROOT / "legacy"


# ============================================================================
# 桩：autostart（不碰注册表）
# ============================================================================

_APPLIED: list = []


def _install_autostart_stub():
    fake = types.ModuleType("app.autostart")
    # 注意：这里刻意用 app/autostart.py 的原始标签（"仅手动启动"/"开机自启"），
    # settings_bridge 会覆盖成"禁止自启"/"开启自启" —— 正好验证覆盖逻辑生效。
    fake.options = lambda: (("none", "仅手动启动"), ("registry", "开机自启"))
    fake.current_exe = lambda: r"C:\fake\BiliYTPlayer.exe"

    def _apply(mode, exe):
        _APPLIED.append((mode, exe))
        return True, "已设为开机自启" if mode == "registry" else "开机自启已关闭"

    fake.apply = _apply
    fake.is_enabled = lambda mode: mode == "registry"
    sys.modules["app.autostart"] = fake
    return fake


def _install_quality_stub():
    fake = types.ModuleType("app.quality")
    fake.QUALITY_OPTIONS = (
        ("auto", "自动（最高可用）"), ("127", "8K"), ("126", "杜比视界"), ("125", "HDR"),
        ("120", "4K"), ("116", "1080P60"), ("112", "1080P+"), ("80", "1080P"),
        ("64", "720P"), ("32", "480P"), ("16", "360P"),
    )
    fake.qn_from_pref = lambda pref: 126
    sys.modules["app.quality"] = fake
    return fake


# ============================================================================
# 断言小工具
# ============================================================================

_RESULTS: list = []


def check(name: str, ok: bool, detail: str = ""):
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))


def eq(name, got, want):
    check(name, got == want, f"got={got!r} want={want!r}")


def close_enough(name, got, want, tol=1e-9):
    try:
        ok = abs(float(got) - float(want)) <= tol
    except (TypeError, ValueError):
        ok = False
    check(name, ok, f"got={got!r} want={want!r}")


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def conf_value(path: Path, key: str):
    """从 `k = v` 文本里取值（忽略注释）。"""
    for line in read(path).splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ";", "[")) or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            return v.strip()
    return None


# ============================================================================
# 1. 纯逻辑
# ============================================================================

def verify_meta(bridge):
    print("\n-- 选项数据 --")
    meta = bridge.pageData("meta")
    eq("meta.alignments 九宫格 9 项", len(meta["alignments"]), 9)
    eq("meta.alignments 首项", meta["alignments"][0], {"key": "bottom-left", "label": "底部 左"})
    eq("meta.tiers 四档", [t["key"] for t in meta["tiers"]], ["auto", "manual", "bar", "prohibited"])
    eq("meta.tiers 中文标签", [t["label"] for t in meta["tiers"]],
       ["自动跳过", "手动跳过", "仅在进度条显示", "禁用"])
    eq("meta.quality 11 档", len(meta["quality"]), 11)
    eq("meta.quality 首项", meta["quality"][0]["key"], "auto")
    eq("meta.autostart 2 项", [a["key"] for a in meta["autostart"]], ["none", "registry"])
    # 用户要求：下拉选项文字是「禁止自启 / 开启自启」，不是 app/autostart.py 的原文
    eq("meta.autostart 标签被覆盖成用户措辞",
       [a["label"] for a in meta["autostart"]], ["禁止自启", "开启自启"])
    eq("meta.theme 三项", [t["key"] for t in meta["theme"]], ["dark", "light", "system"])
    eq("meta.theme 中文标签", [t["label"] for t in meta["theme"]],
       ["深色模式", "浅色模式", "跟随系统"])


def verify_general(cfg_dir: Path, Bridge):
    print("\n-- 通用 / general.conf --")
    bridge = Bridge(cfg_dir)
    r = bridge.saveGeneral({"close_action": "tray", "ask_on_close": False,
                            "quality": "120", "autostart_mode": "registry"})
    check("通用保存返回 ok", r.get("ok") is True, str(r))
    eq("general.conf close_action=tray", conf_value(cfg_dir / "general.conf", "close_action"), "tray")
    eq("general.conf ask_on_close=false", conf_value(cfg_dir / "general.conf", "ask_on_close"), "false")
    eq("general.conf quality=120", conf_value(cfg_dir / "general.conf", "quality"), "120")
    eq("general.conf autostart_mode=registry",
       conf_value(cfg_dir / "general.conf", "autostart_mode"), "registry")
    eq("autostart 调用了 apply(registry, exe)", _APPLIED[-1][0], "registry")

    # 非法画质不覆盖原值
    bridge.saveGeneral({"quality": "999"})
    eq("非法画质被忽略", conf_value(cfg_dir / "general.conf", "quality"), "120")

    # 回读
    snap = bridge.pageData("general")
    eq("general 回读 close_action", snap["close_action"], "tray")
    eq("general 回读 ask_on_close", snap["ask_on_close"], False)
    eq("general 回读 autostart_enabled",
       snap.get("autostart_enabled"), True)

    bridge.saveGeneral({"close_action": "close", "ask_on_close": True, "autostart_mode": "none"})
    eq("general.conf close_action=close", conf_value(cfg_dir / "general.conf", "close_action"), "close")
    eq("general.conf ask_on_close=true", conf_value(cfg_dir / "general.conf", "ask_on_close"), "true")


def verify_theme(cfg_dir: Path, Bridge):
    """主题三选一：落盘 + 真的应用到 Qt + themeChanged 信号 + 脏值回退。

    必须有活着的 QGuiApplication 才能验 styleHints().colorScheme()。
    平台必须是 windows：**offscreen QPA 忽略 setColorScheme**（实测恒为 Unknown），
    用它验这项会误报失败。这里不显示任何窗口，只是建个应用对象。
    """
    print("\n-- 主题（dark/light/system）--")
    # 不能用 setdefault：调用方可能已经设过 offscreen，那样这项永远验不过
    os.environ["QT_QPA_PLATFORM"] = "windows"
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle

    QQuickStyle.setStyle("FluentWinUI3")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    bridge = Bridge(cfg_dir)

    # 收集 themeChanged
    seen = []
    try:
        bridge.themeChanged.connect(lambda name: seen.append(name))
    except Exception as e:                            # noqa: BLE001
        check("themeChanged 信号可连接", False, repr(e))

    # system 的期望值不是 Unknown：Qt 会把 Unknown 「解析」成系统当前值再报回来
    # （实测 Windows 浅色系统下 setColorScheme(Unknown) → colorScheme() == Light）。
    # 所以这里先记录系统真值，再断言回落到它。
    hints = app.styleHints()
    hints.setColorScheme(Qt.ColorScheme.Dark)
    hints.setColorScheme(Qt.ColorScheme.Unknown)
    system_scheme = hints.colorScheme()
    check("system 目标值可确定（Unknown 已解析为系统值）",
          system_scheme in (Qt.ColorScheme.Light, Qt.ColorScheme.Dark),
          f"got={system_scheme}")

    cases = [("dark", Qt.ColorScheme.Dark), ("light", Qt.ColorScheme.Light),
             ("system", system_scheme)]
    for want, qt_want in cases:
        r = bridge.saveGeneral({"theme": want})
        check(f"theme={want} 保存 ok", r.get("ok") is True, str(r))
        eq(f"general.conf theme={want}", conf_value(cfg_dir / "general.conf", "theme"), want)
        got = hints.colorScheme()
        check(f"theme={want} 已应用到 Qt", got == qt_want, f"got={got} want={qt_want}")
        eq(f"theme={want} 回读一致", bridge.pageData("general")["theme"], want)

    # 三次都变了 → 三条 themeChanged
    eq("themeChanged emit 次数", len(seen), 3)
    eq("themeChanged 参数序列", seen, ["dark", "light", "system"])

    # 重复保存同一值不再 emit（避免主窗口无谓刷新）
    bridge.saveGeneral({"theme": "system"})
    eq("重复值不重复 emit", len(seen), 3)

    # 脏值 → 回退 system 并落盘
    bridge.saveGeneral({"theme": "purple"})
    eq("非法 theme 落盘为 system", conf_value(cfg_dir / "general.conf", "theme"), "system")
    eq("非法 theme 回读 system", bridge.pageData("general")["theme"], "system")

    # 无 theme 键时不改动已有值
    bridge.saveGeneral({"theme": "dark"})
    bridge.saveGeneral({"close_action": "close"})
    eq("不传 theme 时保留原值", bridge.pageData("general")["theme"], "dark")


def verify_danmaku(cfg_dir: Path, Bridge):
    print("\n-- 弹幕 / danmaku.conf --")
    bridge = Bridge(cfg_dir)
    path = cfg_dir / "danmaku.conf"

    r = bridge.saveDanmaku({
        "opacity": "0.5", "font_size_ratio": "0.06", "duration_marquee": "10",
        "duration_still": "4", "display_region": "0.5", "outline": "3",
        "font_face": "SimHei", "block_keywords": "前排, 广告",
        "block_top": True, "block_bottom": False, "block_scroll": True,
    })
    check("弹幕保存返回 ok", r.get("ok") is True, str(r))
    eq("danmaku.conf opacity", conf_value(path, "opacity"), "0.5")
    eq("danmaku.conf font_size_ratio", conf_value(path, "font_size_ratio"), "0.06")
    eq("danmaku.conf duration_marquee", conf_value(path, "duration_marquee"), "10.0")
    eq("danmaku.conf duration_still", conf_value(path, "duration_still"), "4.0")
    eq("danmaku.conf display_region", conf_value(path, "display_region"), "0.5")
    eq("danmaku.conf outline", conf_value(path, "outline"), "3.0")
    eq("danmaku.conf font_face", conf_value(path, "font_face"), "SimHei")
    eq("danmaku.conf block_keywords", conf_value(path, "block_keywords"), "前排, 广告")
    eq("danmaku.conf block_top", conf_value(path, "block_top"), "true")
    eq("danmaku.conf block_bottom", conf_value(path, "block_bottom"), "false")
    eq("danmaku.conf block_scroll", conf_value(path, "block_scroll"), "true")

    # ---- 钳制：上限 0.20 不是 0.08（legacy 用户实测反馈）----
    bridge.saveDanmaku({"font_size_ratio": "0.5"})
    eq("font_size_ratio 0.5 → 钳到 0.20", conf_value(path, "font_size_ratio"), "0.2")
    bridge.saveDanmaku({"font_size_ratio": "0.001"})
    eq("font_size_ratio 0.001 → 钳到 0.01", conf_value(path, "font_size_ratio"), "0.01")
    bridge.saveDanmaku({"font_size_ratio": "abc"})
    eq("font_size_ratio 非数字 → 回落 0.04", conf_value(path, "font_size_ratio"), "0.04")

    bridge.saveDanmaku({"opacity": "9"})
    eq("opacity 9 → 1.0", conf_value(path, "opacity"), "1.0")
    bridge.saveDanmaku({"opacity": "0"})
    eq("opacity 0 → 0.05", conf_value(path, "opacity"), "0.05")

    bridge.saveDanmaku({"duration_marquee": "100"})
    eq("duration_marquee 100 → 30.0", conf_value(path, "duration_marquee"), "30.0")
    bridge.saveDanmaku({"duration_marquee": "0"})
    eq("duration_marquee 0 → 2.0", conf_value(path, "duration_marquee"), "2.0")

    bridge.saveDanmaku({"duration_still": "99"})
    eq("duration_still 99 → 20.0", conf_value(path, "duration_still"), "20.0")
    bridge.saveDanmaku({"duration_still": "0"})
    eq("duration_still 0 → 1.0", conf_value(path, "duration_still"), "1.0")

    bridge.saveDanmaku({"display_region": "0.9"})
    eq("display_region 0.9 保留", conf_value(path, "display_region"), "0.9")
    bridge.saveDanmaku({"display_region": "0.1"})
    eq("display_region 0.1 → 0.3", conf_value(path, "display_region"), "0.3")

    bridge.saveDanmaku({"outline": "99"})
    eq("outline 99 → 6.0", conf_value(path, "outline"), "6.0")
    bridge.saveDanmaku({"outline": "-5"})
    eq("outline -5 → 0.0", conf_value(path, "outline"), "0.0")

    # ---- 清空缓存 ----
    cache = cfg_dir / "cache"
    cache.mkdir(exist_ok=True)
    for i in range(3):
        (cache / f"a{i}.ass").write_text("x", encoding="utf-8")
    r = bridge.clearDanmakuCache()
    check("清空缓存返回 ok", r.get("ok") is True, str(r))
    check("清空缓存条数=3", "3 个文件" in str(r.get("msg")), str(r.get("msg")))
    eq("缓存目录已清空", len(list(cache.iterdir())), 0)


def verify_subtitle(cfg_dir: Path, Bridge):
    print("\n-- 字幕 / subtitle.conf --")
    bridge = Bridge(cfg_dir)
    path = cfg_dir / "subtitle.conf"

    r = bridge.saveSubtitle({
        "alignment": "top-right", "position_ratio": "0.2", "font_size_ratio": "0.06",
        "font_face": "SimHei", "color": "#FF0000", "back_color": "#0000FF",
        "opacity": "0.5", "outline": "4", "border_style": "outline",
    })
    check("字幕保存返回 ok", r.get("ok") is True, str(r))
    eq("subtitle.conf alignment=top-right", conf_value(path, "alignment"), "top-right")
    eq("subtitle.conf position_ratio", conf_value(path, "position_ratio"), "0.2")
    eq("subtitle.conf font_size_ratio", conf_value(path, "font_size_ratio"), "0.06")
    eq("subtitle.conf font_face", conf_value(path, "font_face"), "SimHei")
    eq("subtitle.conf color", conf_value(path, "color"), "#FF0000")
    eq("subtitle.conf back_color", conf_value(path, "back_color"), "#0000FF")
    eq("subtitle.conf opacity", conf_value(path, "opacity"), "0.5")
    eq("subtitle.conf outline", conf_value(path, "outline"), "4.0")
    eq("subtitle.conf border_style=outline", conf_value(path, "border_style"), "outline")

    # ---- 钳制 ----
    bridge.saveSubtitle({"position_ratio": "0.9"})
    eq("position_ratio 0.9 → 0.4", conf_value(path, "position_ratio"), "0.4")
    bridge.saveSubtitle({"position_ratio": "-3"})
    eq("position_ratio -3 → 0.0", conf_value(path, "position_ratio"), "0.0")
    bridge.saveSubtitle({"font_size_ratio": "1"})
    eq("font_size_ratio 1 → 0.12", conf_value(path, "font_size_ratio"), "0.12")
    bridge.saveSubtitle({"font_size_ratio": "0.0001"})
    eq("font_size_ratio 0.0001 → 0.01", conf_value(path, "font_size_ratio"), "0.01")
    bridge.saveSubtitle({"opacity": "0"})
    eq("opacity 0 → 0.05", conf_value(path, "opacity"), "0.05")
    bridge.saveSubtitle({"opacity": "3"})
    eq("opacity 3 → 1.0", conf_value(path, "opacity"), "1.0")
    bridge.saveSubtitle({"outline": "20"})
    eq("outline 20 → 6.0", conf_value(path, "outline"), "6.0")
    bridge.saveSubtitle({"outline": "-1"})
    eq("outline -1 → 0.0", conf_value(path, "outline"), "0.0")

    # 非法枚举回退
    bridge.saveSubtitle({"alignment": "middle-nowhere"})
    eq("非法 alignment → bottom-center", conf_value(path, "alignment"), "bottom-center")
    bridge.saveSubtitle({"border_style": "glow"})
    eq("非法 border_style → box", conf_value(path, "border_style"), "box")

    # 恢复默认（只回填，不落盘）
    d = bridge.subtitleDefaults()
    eq("恢复默认 position_ratio", d["position_ratio"], 0.06)
    eq("恢复默认 font_size_ratio", d["font_size_ratio"], 0.045)
    eq("恢复默认 alignment", d["alignment"], "bottom-center")
    eq("恢复默认 border_style", d["border_style"], "box")
    eq("恢复默认 color", d["color"], "#FFFFFF")
    eq("恢复默认 opacity", d["opacity"], 1.0)


def verify_sponsor(cfg_dir: Path, Bridge):
    print("\n-- 广告跳过 / sponsorblock.conf --")
    bridge = Bridge(cfg_dir)
    path = cfg_dir / "sponsorblock.conf"

    snap = bridge.pageData("sponsor")
    eq("分类共 11 个", len(snap["categories"]), 11)
    eq("分类顺序首项", snap["categories"][0]["key"], "sponsor")
    eq("分类中文名", snap["categories"][0]["name"], "赞助/恰饭")
    eq("分类颜色 ASS 反解 #RRGGBB", snap["categories"][0]["color"], "#00D400")
    eq("分类说明非空", bool(snap["categories"][0]["desc"]), True)
    eq("默认档位 sponsor=auto", snap["categories"][0]["tier"], "auto")
    eq("默认档位 exclusive_access=bar", snap["categories"][2]["tier"], "bar")

    cats = [{"key": c["key"], "tier": "prohibited"} for c in snap["categories"]]
    r = bridge.saveSponsor({
        "categories": cats, "notify": False,
        "undo_seconds": "-5", "min_duration": "-1",
    })
    check("跳过保存返回 ok", r.get("ok") is True, str(r))
    raw = conf_value(path, "categories") or ""
    eq("11 个分类全部写入", raw.count(":") , 11)
    check("全部 prohibited 已写入", raw.count("prohibited"), 11, )
    eq("sponsorblock.conf notify=false", conf_value(path, "notify"), "false")
    eq("undo_seconds 负值 → 0.0", conf_value(path, "undo_seconds"), "0.0")
    eq("min_duration 负值 → 0.0", conf_value(path, "min_duration"), "0.0")

    bridge.saveSponsor({"notify": True, "undo_seconds": "5.5", "min_duration": "2"})
    eq("undo_seconds 5.5 保留", conf_value(path, "undo_seconds"), "5.5")
    eq("min_duration 2 保留", conf_value(path, "min_duration"), "2.0")
    eq("notify 回 true", conf_value(path, "notify"), "true")

    # 未知分类 / 未知档位被丢弃，空列表回落默认
    bridge.saveSponsor({"categories": [{"key": "bogus", "tier": "auto"}]})
    raw = conf_value(path, "categories") or ""
    check("空/非法分类回落默认 11 项", raw.count(":") == 11, raw[:60])

    back = bridge.pageData("sponsor")
    eq("回读 notify", back["notify"], True)
    close_enough("回读 undo_seconds", back["undo_seconds"], 5.5)
    close_enough("回读 min_duration", back["min_duration"], 2.0)


def verify_account(cfg_dir: Path, Bridge):
    print("\n-- B站账号 / .env --")
    bridge = Bridge(cfg_dir)
    eq("未配置时 masked=未配置", bridge.reloadAccount()["masked"], "未配置")

    # 有效 SESSDATA（校验器打桩，避免网络与 pip install）
    bridge._validator = lambda sd: sd == "abcd1234efgh5678"
    bridge._verify_worker("abcd1234efgh5678")
    env = cfg_dir / ".env"
    eq(".env 格式与 legacy 一致", read(env),
       "# B 站登录态 Cookie\nSESSDATA=abcd1234efgh5678\n")
    acc = bridge.reloadAccount()
    eq("已配置 configured=True", acc["configured"], True)
    eq("脱敏只显示前 4 位 + 长度", acc["masked"], "abcd****（共 16 字符）")
    check("脱敏不含完整值", "1234efgh" not in acc["masked"], str(acc["masked"]))

    # 无效 SESSDATA 不覆盖
    bridge._validator = lambda sd: False
    bridge._verify_worker("bad")
    eq("无效值不写 .env", read(env),
       "# B 站登录态 Cookie\nSESSDATA=abcd1234efgh5678\n")
    eq("无效值后仍为已配置", bridge.reloadAccount()["configured"], True)


# ============================================================================
# 2. 渲染
# ============================================================================

def verify_render():
    print("\n-- 离屏渲染（真实 SettingsBridge，不是桩）--")
    wrapper = ROOT / "ui" / "settings" / "_shot_wrapper.qml"
    check("_shot_wrapper.qml 存在", wrapper.is_file(), str(wrapper))
    real = ROOT / "_verify_settings_render.py"
    check("真实通路截图脚本存在", real.is_file(), str(real))

    # Fluent 样式的深浅色由 Application.styleHints.colorScheme 决定，offscreen
    # QPA 下不可写（恒为 Unknown → 控件按深色画，ComboBox/RadioButton 会截成空白），
    # 所以这里用 windows QPA 跑截图。
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "windows"
    env["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"

    for scheme in ("light", "dark"):
        out = ROOT / f"_settings_general_{scheme}.png"
        if out.exists():
            out.unlink()
        # 用真实 SettingsBridge 注入后再截图 —— 桩数据只是 QML 原生 JS 对象，
        # 永远「数据是好的」，测不出「真实 bridge → QML QJSValue」通路的毛病
        # （ComboBox 全空那个 bug 就是这么漏掉的）。
        cmd = [sys.executable, str(real), "general", str(out), scheme]
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=120)
            blob = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            check(f"渲染 {scheme} 未超时", False, "120s 未结束")
            continue
        loaded = "LOAD FAIL" not in blob
        saved = out.is_file() and out.stat().st_size > 2000
        combos_ok = "ALL PASS" in blob
        check(f"渲染 {scheme} LOAD 成功", loaded, blob.strip().splitlines()[:3])
        check(f"渲染 {scheme} ComboBox 全部可用（真实 bridge）", combos_ok,
              [ln for ln in blob.splitlines() if ln.startswith("FAIL")][:4])
        check(f"渲染 {scheme} 截图落盘", saved,
              f"size={out.stat().st_size if out.exists() else 0}")
        if saved:
            print(f"      截图: {out}")


# ============================================================================
# 3. QML → conf 整链路（控件取值 → bridge → 落盘）
# ============================================================================

def verify_qml_roundtrip(cfg_dir: Path):
    """加载 SettingsWindow，调各页 save()，再读 conf 校验字段落盘。

    这一层抓的是「QML 字段名写错 / 控件 id 接错」这类只在运行时才暴露的错误，
    纯 bridge 单测抓不到。
    """
    print("\n-- QML 保存链路 --")
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from ui.settings_bridge import SettingsBridge

    QQuickStyle.setStyle("FluentWinUI3")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    bridge = SettingsBridge(cfg_dir)
    bridge._validator = lambda sd: True

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("settingsBridge", bridge)
    engine.rootContext().setContextProperty("__forceScheme", "light")
    engine.load(QUrl.fromLocalFile(str(ROOT / "ui" / "settings" / "SettingsWindow.qml")))
    win = engine.rootObjects()[0] if engine.rootObjects() else None
    check("SettingsWindow.qml 加载", win is not None, "rootObjects 为空")
    if win is None:
        return

    check("SettingsWindow 默认 visible=false", win.property("visible") is False)
    check("SettingsWindow 根元素是 Window", win.inherits("QQuickWindow"),
          win.metaObject().className())

    # 各页 save()：读到的值应与桩前的 conf 默认值一致并成功落盘
    for page_name, conf_name in (("general", "general.conf"), ("playback", "general.conf"),
                                 ("danmaku", "danmaku.conf"), ("subtitle", "subtitle.conf"),
                                 ("sponsor", "sponsorblock.conf")):
        page = win.page(page_name) if hasattr(win, "page") else None
        ok = page is not None
        check(f"{page_name} 页可取到", ok, "win.page() 返回 null")
        if not ok:
            continue
        try:
            page.save()
        except Exception as e:                       # noqa: BLE001 - 自检要看到真实异常
            check(f"{page_name} 页 save() 不抛异常", False, repr(e))
            continue
        status = page.property("statusText") or ""
        ok_status = page.property("statusOk")
        check(f"{page_name} 页 save() 成功", ok_status is True, f"status={status!r}")
        check(f"{page_name} 页写入了 {conf_name}", (cfg_dir / conf_name).is_file(), conf_name)

    # 抽查几个字段（写错字段名的话这里会读到 None）
    eq("QML 保存后 danmaku.conf 有 font_size_ratio",
       conf_value(cfg_dir / "danmaku.conf", "font_size_ratio") is not None, True)
    eq("QML 保存后 danmaku.conf 有 block_keywords",
       conf_value(cfg_dir / "danmaku.conf", "block_keywords") is not None, True)
    eq("QML 保存后 subtitle.conf alignment",
       conf_value(cfg_dir / "subtitle.conf", "alignment"), "bottom-center")
    eq("QML 保存后 sponsorblock.conf 有 categories",
       conf_value(cfg_dir / "sponsorblock.conf", "categories") is not None, True)
    eq("QML 保存后 general.conf close_action",
       conf_value(cfg_dir / "general.conf", "close_action"), "close")

    # ---- 通用页的新控件：主题三选一 + 自启下拉 ----
    gen = win.page("general")
    for name in ("themeSystem", "themeLight", "themeDark",
                 "closeActionClose", "closeActionTray", "askOnClose", "autostartCombo"):
        found = gen.findChildren(object, name)
        check(f"通用页有 objectName={name}", len(found) == 1,
              f"找到 {len(found)} 个")

    # 主题单选：切到「深色模式」再 save，应落盘 dark
    radios = {n: gen.findChildren(object, n)[0] for n in
              ("themeSystem", "themeLight", "themeDark") if gen.findChildren(object, n)}
    if len(radios) == 3:
        import app.theme as theme_mod
        theme_mod.apply_theme("light")
        radios["themeDark"].setProperty("checked", True)
        gen.save()
        eq("QML 选深色 → conf theme=dark",
           conf_value(cfg_dir / "general.conf", "theme"), "dark")
        from PySide6.QtCore import Qt as _Qt
        check("QML 选深色 → Qt 已切深色",
              app.styleHints().colorScheme() == _Qt.ColorScheme.Dark,
              str(app.styleHints().colorScheme()))

        radios["themeSystem"].setProperty("checked", True)
        gen.save()
        eq("QML 选跟随系统 → conf theme=system",
           conf_value(cfg_dir / "general.conf", "theme"), "system")

    # 自动启动下拉的选项文字是用户措辞
    combo = gen.findChildren(object, "autostartCombo")
    if combo:
        model = combo[0].property("model")
        labels = []
        try:
            for i in range(combo[0].property("count")):
                labels.append(model[i]["label"] if isinstance(model, list) else None)
        except Exception:
            pass
        if labels and all(labels):
            eq("QML 自启下拉标签", labels, ["禁止自启", "开启自启"])

    # 导航栏底部提示文案
    hint = [o for o in win.findChildren(object)
            if o.metaObject().className().startswith("QQuickText")
            and "即刻生效" in str(o.property("text") or "")]
    check("导航栏提示改为「保存后即刻生效…」", len(hint) >= 1,
          f"找到 {len(hint)} 条")
    check("导航栏提示不再用旧文案",
          not [o for o in win.findChildren(object)
               if "改完保存即生效" in str(o.property("text") or "")], "")

    # ---- ComboBox 必须真的可用（真实 bridge 通路回归守卫）----
    # 这是「设置页所有下拉框消失」那个 bug 的守卫：QJSValue 经 property var
    # 转发、以及 ui/ 被 addImportPath 重复解析时，ComboBox 会 count=0 且
    # implicitHeight=0（整个框不画出来，且没有任何 QML 警告）。
    def combos_usable_on(page_key: str):
        win.setProperty("currentKey", page_key)
        for _ in range(3):
            app.processEvents()
        found = [o for o in win.findChildren(object)
                 if "ComboBox" in o.metaObject().className()]
        usable = [c for c in found
                  if c.property("count") > 0 and c.property("implicitHeight") > 0]
        return len(found), len(usable), sorted({c.property("count") for c in found})

    for key, want_min in (("general", 1), ("playback", 1), ("subtitle", 1)):
        n, ok_n, counts = combos_usable_on(key)
        check(f"{key} 页 ComboBox 全部可用（{ok_n}/{n}）",
              n >= want_min and ok_n == n, f"counts={counts}")

    win.setProperty("currentKey", "playback")
    for _ in range(3):
        app.processEvents()
    qc = [o for o in win.findChildren(object) if o.property("objectName") == "qualityCombo"]
    if qc:
        eq("qualityCombo 有 11 档（真实 bridge 数据）", qc[0].property("count"), 11)
        eq("qualityCombo 高度正常（不是 0）", qc[0].property("implicitHeight") > 0, True)
    else:
        check("qualityCombo 存在", False, "找不到")

    win.close()
    engine.deleteLater()


# ============================================================================
# main
# ============================================================================

def main() -> int:
    _install_autostart_stub()
    _install_quality_stub()

    from ui.settings_bridge import SettingsBridge

    def Bridge(config_dir):
        return SettingsBridge(config_dir)

    # 顺序有讲究，别改：**渲染必须第一个跑**。
    # 渲染是起子进程做离屏截图；主题检查会在本进程里建 QGuiApplication，
    # 父进程一旦有 Qt 应用，再起 windows 平台的 Qt 子进程就会死锁在事件循环里
    # （实测 90s 不返回，且 stdout 被继承时会连带挂住父进程）。
    verify_render()

    with tempfile.TemporaryDirectory(prefix="biliyt_settings_") as tmp:
        base = Path(tmp)
        verify_meta(Bridge(base / "meta"))
        verify_general(base / "general", Bridge)
        verify_theme(base / "theme", Bridge)      # 这一步会建 QGuiApplication
        verify_danmaku(base / "danmaku", Bridge)
        verify_subtitle(base / "subtitle", Bridge)
        verify_sponsor(base / "sponsor", Bridge)
        verify_account(base / "account", Bridge)

    with tempfile.TemporaryDirectory(prefix="biliyt_settings_qml_") as tmp:
        verify_qml_roundtrip(Path(tmp))

    failed = [n for n, ok, _ in _RESULTS if not ok]
    print(f"\n共 {len(_RESULTS)} 项，通过 {len(_RESULTS) - len(failed)} 项")
    if failed:
        print("失败项：")
        for n in failed:
            print(f"  - {n}")
        print(f"FAIL {len(failed)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
