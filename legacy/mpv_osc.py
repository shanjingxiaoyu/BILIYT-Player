#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mpv_osc.py — 受管的 mpv OSC（屏幕控制器）补丁器

职责：把 mpv 内置的 osc.lua 打上 SponsorBlock 补丁，产出受管 OSC：
  1. 在进度条上按分类绘制彩色区间条
  2. 把右下角时长显示改为「去广告时长 (-省下时长)」

为什么必须改 osc.lua：
  mpv 内置 OSC 只在进度条上画章节「刻度线」（markerF），没有按任意区间着色的能力，
  也无法显示"去除广告后的时长"。要实现 SponsorBlock 那套视觉效果只能改 OSC 本体。

为什么 OSC 自己读环境变量：
  受管 OSC 必须自包含 —— 它独立于 bsponsor.lua 加载（后者负责跳过，前者负责显示），
  两者各自从 BSPONSOR_PAYLOAD 读取同一份载荷，互不依赖。这样即使跳过脚本因故
  未加载，进度条配色依然正确。

设计要点：
  - osc.lua 属于 mpv，许可为 LGPLv2.1+（不在 mpv 的 GPL-only 文件清单中），可随本项目分发。
  - 采用「代码特征锚定」而非硬编码行号：每个锚点必须在原文件中恰好出现一次，
    否则整体放弃打补丁，回退到 mpv 内置 OSC（功能降级，绝不致崩）。
  - 生成物写入配置目录，与 bsponsor.lua 同一机制（内容哈希，变化才重写）。
"""

from pathlib import Path
import sys

__all__ = [
    "OSC_NAME",
    "ANCHORS",
    "BASE_OSC_PATH",
    "PAYLOAD_ENV",
    "build_patched_osc",
    "ensure_managed_osc",
    "verify_patch",
    "patch_report",
]

#: 受管 OSC 落盘文件名
OSC_NAME = "osc_managed.lua"

#: 与 sponsorblock.py 共用的载荷环境变量名（保持同步）
PAYLOAD_ENV = "BSPONSOR_PAYLOAD"

#: 随本项目分发的基线 osc.lua（取自 mpv v0.41.0-244-gaf9c81fa1）
#: 打包后该文件被放在 _MEIPASS 根目录（见 bili_yt_player.spec 的 _add_datas），
#: 因此除模块同级目录外，还要回退到 sys._MEIPASS。
def _locate_base_osc() -> Path:
    candidates = [Path(__file__).resolve().parent / "osc_base.lua"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "osc_base.lua")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "osc_base.lua")
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return candidates[0]   # 返回首选路径（便于报错信息指出期望位置）


BASE_OSC_PATH = _locate_base_osc()


# ============================================================================
# 补丁段
# ============================================================================
# 每个补丁 = (锚点原文, 锚点之后插入的代码)。
# 锚点必须在基线文件中恰好出现一次，否则整体放弃。

# ---- 补丁 1：解析 SponsorBlock 载荷，建立 OSC 侧状态 ---------------------
_PATCH_STATE = (
    "local elements = {}",
    r"""
-- >>> bsponsor: managed state <<<
-- 受管 OSC 自包含：独立于 bsponsor.lua，直接从环境变量读取载荷。
local bsponsor_utils = require "mp.utils"

local bsponsor_state = {
    segments = {},        -- {start=秒, finish=秒, color="&HAABBGGRR&"}
    duration = 0,
    adfree = 0,           -- 去广告后的时长（秒）
    saved = 0,            -- 被跳过的总时长（秒）
}

local function bsponsor_reload()
    bsponsor_state.segments = {}
    bsponsor_state.adfree = 0
    bsponsor_state.saved = 0

    local raw = os.getenv("__PAYLOAD_ENV__")
    if not raw or #raw == 0 then return end
    local ok, payload = pcall(bsponsor_utils.parse_json, raw)
    if not ok or type(payload) ~= "table" then return end

    local dur = mp.get_property_number("duration") or 0
    bsponsor_state.duration = dur

    for _, sg in ipairs(payload.segments or {}) do
        local ss, se = tonumber(sg["start"]), tonumber(sg["finish"])
        -- 双保险：Python 侧已过滤，这里再挡一次 [0,0] / 非法区间
        if ss and se and se > ss then
            bsponsor_state.segments[#bsponsor_state.segments + 1] = {
                start = ss, finish = se,
                color = sg["color"] or "&H00D400&",
            }
            bsponsor_state.saved = bsponsor_state.saved + (se - ss)
        end
    end

    if dur > 0 then
        bsponsor_state.adfree = math.max(0, dur - bsponsor_state.saved)
    end
end

bsponsor_reload()
mp.register_event("start-file", bsponsor_reload)
mp.observe_property("duration", "number", function() bsponsor_reload() end)
-- <<< bsponsor: managed state >>>
""".replace("__PAYLOAD_ENV__", PAYLOAD_ENV),
)

# ---- 补丁 2：进度条彩色区间条 --------------------------------------------
# 锚点 `-- add tooltip` 位于 render_elements() 中 slider 绘制完成之后。
# 关键：每个色块必须用**独立的 ass 对象**并自带 \pos/\an 再 merge。
# 原因见 mpv 的 player/lua/assdraw.lua:9 —— new_event() 只写一个换行，
# 而 ASS 中换行即新事件行、不继承 \pos，直接用 elem_ass:new_event() 追加
# 会把图形画到无定位的默认角落（实测表现为跑到窗口左上角）。
_PATCH_BAR = (
    "            -- add tooltip",
    r"""
            -- >>> bsponsor: colored segment bars <<<
            -- 只在进度条上画。判据用 element.type == "slider"：
            -- new_element() 并不设置 element.name 字段（见 osc.lua 的 new_element 实现），
            -- 而全文件只有一个 slider 元素（new_element("seekbar", "slider")），
            -- 因此 type 是既可靠又唯一的判据。
            if element.type == "slider" then
                local bdur = mp.get_property_number("duration") or 0
                if bdur > 0 then
                    for _, sg in ipairs(bsponsor_state.segments) do
                        local x0 = get_slider_ele_pos_for(element, 100 * sg.start / bdur)
                        local x1 = get_slider_ele_pos_for(element, 100 * sg.finish / bdur)
                        -- 独立 ass 对象：必须自带 \pos/\an，否则 ASS 新事件行无定位
                        local o = assdraw.ass_new()
                        o:append("{}")
                        o:new_event()
                        o:pos(elem_geo.x, elem_geo.y)
                        o:an(elem_geo.an)
                        o:append("{\\c" .. sg.color .. "\\alpha&H20&}")
                        o:draw_start()
                        o:rect_cw(x0, foV, x1, elem_geo.h - foV)
                        o:draw_stop()
                        elem_ass:merge(o)
                    end
                end
            end
            -- <<< bsponsor: colored segment bars >>>
""",
)

# ---- 补丁 3：右下角显示「去广告时长」 ------------------------------------
# 用「区域替换」：tc_right 的 ne.content 定义横跨多行，单靠插入无法改写，
# 因此以两个各自唯一的锚点界定替换范围（含首尾锚点本身）。
#   start: ne.visible = ...   （duration>0 的可见性判断，全文件唯一）
#   end  : function () state.rightTC_trem = ... end （tc_right 的点击回调，全文件唯一）
# 这两行之间的整段（正是原 duration 显示逻辑）被整体替换。
_PATCH_TIME = (
    '    ne.visible = (mp.get_property_number("duration", 0) > 0)',
    '        function () state.rightTC_trem = not state.rightTC_trem end',
    r"""    -- >>> bsponsor: ad-free duration >>>
    ne.visible = (mp.get_property_number("duration", 0) > 0)
    ne.content = function ()
        -- 有片段数据时显示「去广告时长 (-省下时长)」，否则维持 mpv 原行为
        if bsponsor_state.adfree > 0 and bsponsor_state.saved >= 1 then
            return mp.format_time(bsponsor_state.adfree)
                   .. " (-" .. mp.format_time(bsponsor_state.saved) .. ")"
        end
        if state.rightTC_trem then
            local minus = user_opts.unicodeminus and UNICODE_MINUS or "-"
            local property = user_opts.remaining_playtime and "playtime-remaining"
                                                           or "time-remaining"
            if state.tc_ms then
                return (minus..mp.get_property_osd(property .. "/full"))
            else
                return (minus..mp.get_property_osd(property))
            end
        else
            if state.tc_ms then
                return (mp.get_property_osd("duration/full"))
            else
                return (mp.get_property_osd("duration"))
            end
        end
    end
    -- <<< bsponsor: ad-free duration >>>
    ne.eventresponder["mbtn_left_up"] =
        function () state.rightTC_trem = not state.rightTC_trem end""",
)

#: 插入型补丁：(锚点, 锚点之后插入的代码)
PATCH_BLOCKS = (_PATCH_STATE, _PATCH_BAR)

#: 区域替换型补丁：(起始锚点, 结束锚点, 替换整段的新代码)
REPLACE_BLOCKS = (_PATCH_TIME,)

#: 全部锚点（插入型的 + 区域型的起止），供校验使用
ANCHORS = tuple(anchor for anchor, _ in PATCH_BLOCKS) + tuple(
    a for pair in REPLACE_BLOCKS for a in pair[:2]
)

#: 补丁标记，供完整性校验
MARKERS = (
    "bsponsor: managed state",
    "bsponsor: colored segment bars",
    "bsponsor: ad-free duration",
)


# ============================================================================
# 打补丁
# ============================================================================

def build_patched_osc(base_source: str) -> tuple:
    """给基线 osc.lua 打补丁。

    返回 (patched_source, ok, reason)：
      - ok=True  → patched_source 为完整可用的补丁版源码
      - ok=False → patched_source 原样返回，reason 说明原因
    任何锚点缺失或重复都判为失败，调用方应回退到 mpv 内置 OSC。
    """
    if not base_source:
        return base_source, False, "基线 osc.lua 为空"

    # 先校验全部锚点，避免部分插入产生半成品
    for anchor in ANCHORS:
        count = base_source.count(anchor)
        if count != 1:
            return base_source, False, (
                f"锚点出现 {count} 次（应为 1 次），放弃打补丁: {anchor.strip()[:60]}"
            )

    # 区域型补丁额外校验：结束锚点必须位于起始锚点之后
    for start_anchor, end_anchor, _ in REPLACE_BLOCKS:
        if base_source.index(end_anchor) < base_source.index(start_anchor):
            return base_source, False, (
                f"区域锚点顺序异常: {start_anchor.strip()[:40]} 之后找不到结束锚点"
            )

    patched = base_source
    # 插入型：锚点之后追加
    for anchor, patch in PATCH_BLOCKS:
        idx = patched.index(anchor)
        end = idx + len(anchor)
        patched = patched[:end] + "\n" + patch + patched[end:]

    # 区域替换型：用起止锚点界定范围，整段换掉（含首尾锚点）
    for start_anchor, end_anchor, replacement in REPLACE_BLOCKS:
        i = patched.index(start_anchor)
        j = patched.index(end_anchor, i)
        j_end = j + len(end_anchor)
        patched = patched[:i] + replacement + patched[j_end:]

    return patched, True, "ok"


def verify_patch(patched_source: str) -> tuple:
    """校验补丁版源码自身完整（标记成对出现）。"""
    if not patched_source:
        return False, "源码为空"
    for marker in MARKERS:
        if marker not in patched_source:
            return False, f"缺少补丁标记: {marker}"
        if patched_source.count(">>> " + marker) != patched_source.count("<<< " + marker):
            return False, f"补丁标记不成对: {marker}"
    return True, "ok"


def patch_report(base_source: str) -> dict:
    """诊断信息：每个锚点的命中次数，便于排查 mpv 升级导致的失效。"""
    return {
        "anchors": {a.strip()[:60]: base_source.count(a) for a in ANCHORS},
        "base_size": len(base_source or ""),
        "base_sha256": __import__("hashlib").sha256(
            (base_source or "").encode("utf-8")).hexdigest(),
    }


def ensure_managed_osc(config_dir: Path, log=None) -> tuple:
    """生成（必要时）受管 OSC，返回 (脚本路径或None, 状态说明)。

    None 表示应回退到 mpv 内置 OSC（此时调用方不得传 --osc=no）。
    """
    def _note(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass

    try:
        if not BASE_OSC_PATH.is_file():
            _note(f"[!] 缺少基线 osc.lua（{BASE_OSC_PATH.name}），彩色进度条不可用")
            return None, "missing-base"

        base = BASE_OSC_PATH.read_text(encoding="utf-8")
        patched, ok, reason = build_patched_osc(base)
        if not ok:
            _note(f"[!] OSC 补丁失败，回退内置 OSC: {reason}")
            return None, reason

        ok2, reason2 = verify_patch(patched)
        if not ok2:
            _note(f"[!] OSC 补丁自检失败，回退内置 OSC: {reason2}")
            return None, reason2

        path = Path(config_dir) / OSC_NAME
        try:
            if path.is_file() and path.read_text(encoding="utf-8") == patched:
                return path, "cached"
            Path(config_dir).mkdir(parents=True, exist_ok=True)
            # newline="" + UTF-8 无 BOM：Lua 的 load() 不接受 BOM
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(patched)
            return path, "written"
        except OSError as e:
            _note(f"[!] 受管 OSC 写入失败，回退内置 OSC: {e}")
            return None, "write-failed"
    except Exception as e:
        _note(f"[!] 受管 OSC 生成异常，回退内置 OSC: {e}")
        return None, "exception"
