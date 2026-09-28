#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""danmaku / subtitle 模块单元测试。

运行：
    python -m unittest discover -s legacy/tests -t .
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import danmaku as dm  # noqa: E402

try:
    import PIL  # noqa: F401
    _PIL = True
except ImportError:
    _PIL = False


def _mpv_exe():
    cand = Path(__file__).resolve().parent.parent.parent / "mpv-portable" / "mpv.exe"
    return str(cand) if cand.is_file() else None


_MPV = _mpv_exe()


# 构造一段乱序的合成弹幕 XML（B 站真实返回就是乱序的）
def _xml(entries) -> bytes:
    """entries: [(time, mode, size, color, text)]

    p 属性顺序必须与 B 站一致：时间, 模式, 字号, 颜色, 时间戳, 池, uid, dmid, ...
    （早期版本我把 mode 写到了第 2 位之外，导致解析结果错位。）
    """
    body = "".join(
        f'<d p="{t},{m},{sz},{c},1700000000,0,uid{dmid},{dmid},10">{txt}</d>'
        for dmid, (t, m, sz, c, txt) in enumerate(entries)
    )
    return ('<?xml version="1.0" encoding="UTF-8"?><i>'
            f'<chatserver>chat.bilibili.com</chatserver>{body}</i>').encode("utf-8")


class TestParseXml(unittest.TestCase):

    def test_parses_basic_fields(self):
        raw = _xml([(1.5, 1, 25, 16777215, "hello"),
                    (2.0, 5, 25, 16711680, "top")])
        items = dm.parse_xml(raw)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["start"], 1.5)
        self.assertEqual(items[0]["mode"], 1)
        self.assertEqual(items[0]["size"], 25)
        self.assertEqual(items[0]["color"], 16777215)
        self.assertEqual(items[0]["text"], "hello")

    def test_preserves_unicode(self):
        items = dm.parse_xml(_xml([(1.0, 1, 25, 0xFFFFFF, "中文弹幕😀")]))
        self.assertEqual(items[0]["text"], "中文弹幕😀")

    def test_empty_input(self):
        self.assertEqual(dm.parse_xml(b""), [])
        self.assertEqual(dm.parse_xml(b"<i></i>"), [])

    def test_malformed_xml_returns_empty(self):
        self.assertEqual(dm.parse_xml(b"<i><d p="), [])
        self.assertEqual(dm.parse_xml(b"not xml at all"), [])
        self.assertEqual(dm.parse_xml(b"\x00\x01\x02binary"), [])

    def test_drops_special_modes_7_8(self):
        """高级弹幕(mode 7/8)内容是嵌套 JSON/XML，不是纯文本，必须丢弃。"""
        raw = _xml([(1.0, 1, 25, 0xFFFFFF, "normal"),
                    (2.0, 7, 25, 0xFFFFFF, '["advanced"]'),
                    (3.0, 8, 25, 0xFFFFFF, "<xml/>")])
        items = dm.parse_xml(raw)
        self.assertEqual([i["text"] for i in items], ["normal"])

    def test_drops_short_p_attribute(self):
        raw = b'<i><d p="1.0,1,25">x</d><d p="2.0,1,25,16777215">y</d></i>'
        items = dm.parse_xml(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "y")

    def test_drops_non_numeric_and_empty_text(self):
        raw = (b'<i><d p="abc,1,25,16777215">x</d>'
               b'<d p="1,1,25,16777215"></d>'
               b'<d p="2,1,25,16777215">   </d></i>')
        self.assertEqual(dm.parse_xml(raw), [])

    def test_respects_max_danmaku(self):
        big = _xml([(float(i), 1, 25, 0xFFFFFF, f"d{i}") for i in range(dm.MAX_DANMAKU + 500)])
        items = dm.parse_xml(big)
        self.assertLessEqual(len(items), dm.MAX_DANMAKU)


class TestAssHelpers(unittest.TestCase):

    def test_time_format(self):
        self.assertEqual(dm._ass_time(0), "0:00:00.00")
        self.assertEqual(dm._ass_time(61.5), "0:01:01.50")
        self.assertEqual(dm._ass_time(3661.25), "1:01:01.25")

    def test_time_negative_clamped(self):
        self.assertEqual(dm._ass_time(-5), "0:00:00.00")

    def test_color_is_bgr(self):
        """B 站十进制是 RGB，ASS 需要 BGR。纯红 0xFF0000 -> &H000000FF&"""
        self.assertEqual(dm._ass_color(0xFF0000), "&H000000FF&")
        self.assertEqual(dm._ass_color(0x00FF00), "&H0000FF00&")
        self.assertEqual(dm._ass_color(0xFFFFFF), "&H00FFFFFF&")
        self.assertEqual(dm._ass_color(0x000000), "&H00000000&")

    def test_color_tolerates_bad_input(self):
        self.assertEqual(dm._ass_color("nope"), "&H00FFFFFF&")

    def test_alpha_from_opacity(self):
        self.assertEqual(dm._ass_alpha(1.0), "00")
        self.assertEqual(dm._ass_alpha(0.0), "FF")
        self.assertEqual(dm._ass_alpha(0.8), "33")

    def test_escape_protects_ass_tags(self):
        self.assertEqual(dm._escape_ass("a{b}c"), "a\\{b\\}c")
        self.assertEqual(dm._escape_ass("a\\b"), "a\\\\b")
        self.assertEqual(dm._escape_ass("a\nb"), "a b")

    def test_text_width_cjk_wider(self):
        self.assertGreater(dm._text_width("中文", 25), dm._text_width("ab", 25))


class TestToAss(unittest.TestCase):

    def _items(self, entries):
        return [{"start": t, "mode": m, "size": sz, "color": c, "text": txt}
                for (t, m, sz, c, txt) in entries]

    def test_header_and_playres(self):
        ass = dm.to_ass(self._items([(1.0, 1, 25, 0xFFFFFF, "x")]), 1280, 720)
        self.assertTrue(ass.startswith("[Script Info]"))
        self.assertIn("PlayResX: 1280", ass)
        self.assertIn("PlayResY: 720", ass)
        self.assertIn("[V4+ Styles]", ass)
        self.assertIn("[Events]", ass)
        self.assertNotIn("\ufeff", ass)      # 无 BOM

    def test_scroll_uses_move(self):
        ass = dm.to_ass(self._items([(1.0, 1, 25, 0xFFFFFF, "hi")]), 1920, 1080)
        self.assertIn("\\move(", ass)

    def test_top_and_bottom_use_an(self):
        ass = dm.to_ass(self._items([(1.0, 5, 25, 0xFFFFFF, "t"),
                                     (2.0, 4, 25, 0xFFFFFF, "b")]), 1920, 1080)
        self.assertIn("\\an8", ass)
        self.assertIn("\\an2", ass)

    def test_empty_items_still_valid_ass(self):
        ass = dm.to_ass([], 1920, 1080)
        self.assertIn("[Script Info]", ass)
        self.assertEqual(dm.count_events(ass), 0)

    def test_display_region_keeps_bottom_clear(self):
        """底部要留给原生字幕与 OSC，弹幕不应铺满整屏。"""
        items = self._items([(float(i), 1, 25, 0xFFFFFF, f"d{i}") for i in range(200)])
        ass = dm.to_ass(items, 1920, 1080, {"display_region": 0.5, "duration_marquee": 30})
        # 所有 \move 的 y 坐标都应落在上半屏
        import re
        ys = [int(m.group(1)) for m in re.finditer(r"\\move\(\d+,(\d+),", ass)]
        self.assertTrue(ys)
        self.assertLess(max(ys), 1080 * 0.55, f"弹幕越过了上半屏: max y={max(ys)}")

    # ---- 关键回归：乱序输入 + 轨道分配 ----

    def test_unsorted_input_still_high_retention(self):
        """B 站 XML 不是按时间排序的。

        真实 bug：生产实现直接按输入顺序分配轨道，而 B 站首条可能是 495s、
        第二条 50s；乱序时间戳会把轨道空闲时间向后推，导致大量弹幕被判「无轨道」，
        实测保留率只有 27.8%。to_ass 必须先自行排序 —— 修复后为 100%。
        """
        # 构造严重乱序的密集弹幕
        entries = [(float(i * 3 % 500), 1, 25, 0xFFFFFF, f"danmaku-{i}")
                   for i in range(600)]
        items = self._items(entries)
        # 断言输入确实乱序（否则这个测试就没意义了）
        starts = [i["start"] for i in items]
        self.assertNotEqual(starts, sorted(starts), "测试数据应当乱序")

        ass = dm.to_ass(items, 1920, 1080, {"duration_marquee": 8.0})
        n = dm.count_events(ass)
        retention = 100.0 * n / len(items)
        self.assertGreater(retention, 90.0,
                           f"乱序输入保留率仅 {retention:.1f}%（排序缺失会导致约 28%）")

    def test_sorted_dense_input_retention_is_high(self):
        """密集但有序时应接近全量保留（验证 Danmaku2ASS 式轨道复用）。"""
        entries = [(i * 0.5, 1, 25, 0xFFFFFF, f"d{i}") for i in range(400)]
        items = self._items(entries)
        ass = dm.to_ass(items, 1920, 1080, {"duration_marquee": 12.0})
        retention = 100.0 * dm.count_events(ass) / len(items)
        self.assertGreater(retention, 95.0, f"保留率仅 {retention:.1f}%")

    def test_lane_reuse_beats_fixed_occupancy(self):
        """确保没有退化成「轨道固定占用 N 秒」的实现。"""
        # 300 条弹幕在 10 秒内，若轨道固定占用 12 秒，最多只能放 ~n_lanes 条
        items = self._items([(i * 0.03, 1, 25, 0xFFFFFF, f"d{i}") for i in range(300)])
        ass = dm.to_ass(items, 1920, 1080, {"duration_marquee": 12.0})
        self.assertGreater(dm.count_events(ass), 100,
                           "轨道复用失效，疑似退化为固定占用")

    def test_lanes_stack_from_top_like_bilibili(self):
        """轨道自上而下紧贴堆叠（与 B 站官方一致）。

        注：早期我曾断言「各轨占用均衡」，但那是错的 ——
        均匀分布会让弹幕撒满整个区域，观感稀疏、不像 B 站。
        用户实测后要求改回官方那种自上而下紧贴的堆叠方式。
        """
        import collections
        import re
        items = self._items([(i * 0.1, 1, 25, 0xFFFFFF, f"d{i}") for i in range(600)])
        ass = dm.to_ass(items, 1920, 1080, {"duration_marquee": 12.0})
        ys = [int(m.group(1)) for m in re.finditer(r"\\move\(\d+,(\d+),", ass)]
        self.assertGreater(len(ys), 100)
        counts = collections.Counter(ys)
        ordered = [counts[y] for y in sorted(counts)]
        self.assertGreater(ordered[0], ordered[-1],
                           "顶部轨道应比底部用得多（自上而下堆叠）")
        self.assertGreater(len(counts), 3, "只用了很少的轨道")

    def test_no_danmaku_clipped_at_top(self):
        """第一条轨道的 y 必须至少留出一个行高，否则文字会被顶边裁掉。"""
        import re
        items = self._items([(i * 0.05, 1, 25, 0xFFFFFF, f"d{i}") for i in range(200)])
        items += [{"start": 0.0, "mode": 5, "size": 25, "color": 0xFFFFFF, "text": "t"}]
        items = [x for x in items if isinstance(x, dict)]
        ass = dm.to_ass(items, 1920, 1080, {"font_size_ratio": 0.025})
        font = int(re.search(r"Style: DM,[^,]+,(\d+)", ass).group(1))
        lane_h = int(font * 1.2)
        ys = [int(m.group(1)) for m in re.finditer(r"\\move\(\d+,(\d+),", ass)]
        self.assertTrue(ys)
        self.assertGreaterEqual(min(ys), font, "滚动弹幕顶到画面外")

    def test_block_flags(self):
        items = self._items([(1.0, 1, 25, 0xFFFFFF, "scroll"),
                             (2.0, 5, 25, 0xFFFFFF, "top"),
                             (3.0, 4, 25, 0xFFFFFF, "bottom")])
        ass = dm.to_ass(items, 1920, 1080, {"block_scroll": True, "block_top": True})
        self.assertNotIn("scroll", ass)
        self.assertNotIn("top", ass)
        self.assertIn("bottom", ass)

    def test_block_keywords(self):
        items = self._items([(1.0, 1, 25, 0xFFFFFF, "good"),
                             (2.0, 1, 25, 0xFFFFFF, "badword here")])
        ass = dm.to_ass(items, 1920, 1080, {"block_keywords": "badword"})
        self.assertIn("good", ass)
        self.assertNotIn("badword", ass)

    def test_font_size_scales_with_height(self):
        items = self._items([(1.0, 1, 25, 0xFFFFFF, "x")])
        a = dm.to_ass(items, 1920, 1080, {"font_size_ratio": 0.02})
        b = dm.to_ass(items, 1920, 2160, {"font_size_ratio": 0.02})
        import re
        fa = int(re.search(r"Style: DM,[^,]+,(\d+)", a).group(1))
        fb = int(re.search(r"Style: DM,[^,]+,(\d+)", b).group(1))
        self.assertAlmostEqual(fb / fa, 2.0, delta=0.15)

    def test_handles_garbage_items(self):
        items = [None, {}, {"start": "x"}, {"start": 1.0, "mode": 1, "text": ""},
                 {"start": 2.0, "mode": 1, "text": "ok"}]
        ass = dm.to_ass(items, 1920, 1080)
        self.assertEqual(dm.count_events(ass), 1)


class TestReportedBugs(unittest.TestCase):
    """用户实测反馈的三个 bug 的回归测试。"""

    def _items(self, n=1, size=25):
        return [{"start": float(i), "mode": 1, "size": size,
                 "color": 0xFFFFFF, "text": f"d{i}"} for i in range(n)]

    @staticmethod
    def _fs(ass):
        import re
        return int(re.search(r"\\fs(\d+)", ass).group(1))

    # ---- bug 3：4K 视频弹幕小到不能看 ----

    def test_bug3_same_visual_size_at_all_resolutions(self):
        """字号必须按分辨率等比缩放。

        真实 bug：B 站 XML 的 size 字段是为 1920x1080 设计的绝对像素（常见 25），
        却被直接当作 ASS 字号使用。结果 1080p 是 2.3% 屏高，
        4K 只有 1.2% —— 用户实测「4K 弹幕小到不能看」。
        """
        cfg = {"font_size_ratio": 0.05, "scale_xml_size": True, "display_region": 0.85}
        pcts = []
        for W, H in ((1920, 1080), (4096, 2048), (3840, 2160), (1280, 720)):
            with self.subTest(res=f"{W}x{H}"):
                fs = self._fs(dm.to_ass(self._items(), W, H, cfg))
                pct = fs / H
                pcts.append(pct)
                self.assertAlmostEqual(pct, 0.05, delta=0.006,
                                       msg=f"{W}x{H} 字号占屏 {pct:.1%}，应约 5%")
        # 所有分辨率的观感应一致
        self.assertLess(max(pcts) - min(pcts), 0.01,
                        f"各分辨率观感不一致: {[round(p,3) for p in pcts]}")

    def test_bug3_four_k_is_not_smaller_than_1080p(self):
        cfg = {"font_size_ratio": 0.05, "scale_xml_size": True}
        small = self._fs(dm.to_ass(self._items(), 1920, 1080, cfg))
        big = self._fs(dm.to_ass(self._items(), 3840, 2160, cfg))
        self.assertGreater(big, small * 1.8, "4K 字号未按比例放大")

    def test_bug3_scale_can_be_disabled(self):
        """关掉 scale_xml_size 后，所有弹幕都退回统一的基准字号，
        不再体现各自原有的相对大小（供对照/排障用）。"""
        base = {"font_size_ratio": 0.05, "scale_xml_size": False}
        small = self._fs(dm.to_ass(self._items(1, size=25), 1920, 1080, base))
        large = self._fs(dm.to_ass(self._items(1, size=36), 1920, 1080, base))
        self.assertEqual(small, large,
                         "关闭缩放后不同 size 应得到相同字号")
        # 但基准字号仍随分辨率缩放（否则 4K 又变小了）
        big = self._fs(dm.to_ass(self._items(1, size=25), 3840, 2160, base))
        self.assertGreater(big, small * 1.8)

    def test_bug3_relative_size_differences_kept(self):
        """弹幕之间原有的相对大小差异要保留（有人发大字号）。"""
        cfg = {"font_size_ratio": 0.05, "scale_xml_size": True}
        a = self._fs(dm.to_ass(self._items(1, size=25), 1920, 1080, cfg))
        b = self._fs(dm.to_ass(self._items(1, size=36), 1920, 1080, cfg))
        self.assertGreater(b, a, "大字号弹幕未体现出来")

    def test_bug3_font_size_ratio_is_respected(self):
        """font_size_ratio 必须真正改变字号（用户说改了没反应）。"""
        sizes = []
        for r in (0.03, 0.05, 0.08, 0.15):
            fs = self._fs(dm.to_ass(self._items(), 1920, 1080,
                                   {"font_size_ratio": r, "scale_xml_size": True}))
            sizes.append(fs)
        self.assertEqual(sizes, sorted(sizes), f"字号未随比例单调变化: {sizes}")
        self.assertGreater(sizes[-1], sizes[0] * 3,
                           f"比例变化未充分反映到字号: {sizes}")

    # ---- bug 1：GUI 改了字号保存后仍是默认值 ----

    def test_bug1_cache_key_includes_style(self):
        """缓存文件名必须含样式指纹。

        真实 bug：文件名只有 `<cid>_<W>x<H>.danmaku.ass`。
        用户改字号后旧缓存仍被命中，设置看起来「完全没生效」。
        """
        items = self._items(1)
        with tempfile.TemporaryDirectory() as d:
            a = dm.ensure_ass(9, 1920, 1080, config_dir=Path(d), items=items,
                              cfg={"font_size_ratio": 0.05})
            b = dm.ensure_ass(9, 1920, 1080, config_dir=Path(d), items=items,
                              cfg={"font_size_ratio": 0.08})
            self.assertNotEqual(a, b, "改字号后仍命中同一缓存文件")
            self.assertTrue(a.is_file() and b.is_file())

    def test_bug1_each_style_key_changes_cache(self):
        """任一影响渲染的样式项变化都必须产生新的缓存文件。"""
        items = self._items(1)
        base = {"font_size_ratio": 0.05, "display_region": 0.55, "opacity": 0.8,
                "duration_marquee": 12.0, "outline": 2.0}
        with tempfile.TemporaryDirectory() as d:
            ref = dm.ensure_ass(7, 1920, 1080, config_dir=Path(d),
                                items=items, cfg=base)
            for key, val in (("font_size_ratio", 0.09), ("display_region", 0.9),
                             ("opacity", 0.3), ("duration_marquee", 6.0),
                             ("outline", 4.0), ("block_scroll", True)):
                with self.subTest(key=key):
                    cfg = dict(base)
                    cfg[key] = val
                    p = dm.ensure_ass(7, 1920, 1080, config_dir=Path(d),
                                      items=items, cfg=cfg)
                    self.assertNotEqual(p, ref, f"改 {key} 未产生新缓存")

    # ---- bug 2：弹幕分布不如之前 ----

    def test_bug2_lanes_fill_from_top(self):
        """轨道应从最上方开始紧贴堆叠（B 站官方观感）。

        真实 bug：我曾把选择策略改成「选空闲最久的轨道」以求均匀分布，
        结果弹幕被均匀撒满整个区域，看起来稀疏、不像 B 站（用户要求改回）。
        """
        import collections
        import re
        items = self._items(400)
        items = [dict(x, start=i * 0.05) for i, x in enumerate(items)]
        ass = dm.to_ass(items, 1920, 1080,
                        {"display_region": 0.85, "font_size_ratio": 0.05,
                         "duration_marquee": 12.0})
        ys = [int(m.group(1)) for m in re.finditer(r"\\move\(\d+,(\d+),", ass)]
        self.assertGreater(len(ys), 50)
        cnt = collections.Counter(ys)
        ordered = [cnt[y] for y in sorted(cnt)]
        # 从上到下应大体递减（上面的轨道用得多）
        self.assertGreaterEqual(ordered[0], ordered[-1],
                                f"顶部轨道未优先使用: {list(zip(sorted(cnt), ordered))[:5]}")

    def test_bug2_uses_topmost_free_lane(self):
        """直接验证 _pick_lane 的语义：返回第一条空闲轨道。"""
        self.assertEqual(dm._pick_lane([0.0, 5.0, 0.0], 1.0), 0)
        self.assertEqual(dm._pick_lane([5.0, 0.0, 0.0], 1.0), 1)
        self.assertIsNone(dm._pick_lane([5.0, 5.0], 1.0))

    def test_bug2_not_flat_across_lanes(self):
        """轨道占用必须「上重下轻」，不能是平的。

        真实 bug：我曾改为「选空闲最久的轨道」，结果各轨占用几乎完全均匀
        （实测顶轨 108 条 / 底轨 107 条 —— 基本是平的），
        弹幕被均匀撒满整个区域，观感稀疏、不像 B 站。
        改回「最上面第一条空闲轨道」后为 132 / 38，明显上重下轻。
        这里用顶/底轨比值区分两种策略。
        """
        import collections
        import re
        # 密集场景才能体现两种策略的差异
        items = [{"start": i * 0.08, "mode": 1, "size": 25,
                  "color": 0xFFFFFF, "text": f"d{i}"} for i in range(1500)]
        ass = dm.to_ass(items, 1920, 1080,
                        {"display_region": 0.85, "font_size_ratio": 0.05,
                         "duration_marquee": 12.0})
        ys = [int(m.group(1)) for m in re.finditer(r"\\move\(\d+,(\d+),", ass)]
        self.assertGreater(len(ys), 500)
        counts = collections.Counter(ys)
        ordered = [counts[y] for y in sorted(counts)]
        self.assertGreater(len(ordered), 3)
        ratio = ordered[0] / max(1, ordered[-1])
        self.assertGreater(ratio, 1.5,
                           f"顶/底轨比值仅 {ratio:.2f}，分布过于均匀"
                           f"（疑似回到「选空闲最久」策略）: {ordered}")


class TestFetchXml(unittest.TestCase):
    """网络层：任何异常都必须返回 b""，绝不抛出。"""

    def _resp(self, body, enc=""):
        class R:
            def __init__(self):
                self._b = body
                self.headers = {"Content-Encoding": enc} if enc else {}
            def read(self):
                return self._b
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return R()

    def test_deflate_is_decompressed(self):
        """B 站返回 raw deflate（无 zlib 头），必须用 -15 解压。

        真实踩过的坑：不解压直接 decode('utf-8') 会 UnicodeDecodeError。
        """
        payload = _xml([(1.0, 1, 25, 0xFFFFFF, "中文")])
        comp = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = comp.compress(payload) + comp.flush()
        with mock.patch("urllib.request.urlopen", return_value=self._resp(packed, "deflate")):
            out = dm.fetch_xml(123)
        self.assertEqual(out, payload)

    def test_plain_xml_passthrough(self):
        payload = _xml([(1.0, 1, 25, 0xFFFFFF, "x")])
        with mock.patch("urllib.request.urlopen", return_value=self._resp(payload)):
            self.assertEqual(dm.fetch_xml(123), payload)

    def test_gzip_supported(self):
        import gzip
        payload = _xml([(1.0, 1, 25, 0xFFFFFF, "x")])
        with mock.patch("urllib.request.urlopen", return_value=self._resp(gzip.compress(payload), "gzip")):
            self.assertEqual(dm.fetch_xml(123), payload)

    def test_undeclared_deflate_is_sniffed(self):
        """少数中间层不声明 Content-Encoding 但仍压缩，需兜底嗅探。"""
        payload = _xml([(1.0, 1, 25, 0xFFFFFF, "x")])
        comp = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = comp.compress(payload) + comp.flush()
        with mock.patch("urllib.request.urlopen", return_value=self._resp(packed, "")):
            self.assertEqual(dm.fetch_xml(123), payload)

    def test_network_error_returns_empty(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("no net")):
            self.assertEqual(dm.fetch_xml(123), b"")

    def test_timeout_returns_empty(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
            self.assertEqual(dm.fetch_xml(123), b"")

    def test_empty_body_returns_empty(self):
        with mock.patch("urllib.request.urlopen", return_value=self._resp(b"", "deflate")):
            self.assertEqual(dm.fetch_xml(123), b"")

    def test_corrupt_deflate_returns_empty(self):
        with mock.patch("urllib.request.urlopen", return_value=self._resp(b"\x00\x01garbage", "deflate")):
            self.assertEqual(dm.fetch_xml(123), b"")


class TestConfig(unittest.TestCase):

    def test_defaults_and_template_generation(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = dm.load_config(Path(d))
            self.assertTrue((Path(d) / "danmaku.conf").exists())
            self.assertTrue(cfg["enabled"])
            self.assertEqual(cfg["display_region"], 0.55)
            self.assertEqual(cfg["duration_marquee"], 12.0)
            self.assertEqual(cfg["font_size_ratio"], 0.05)
            self.assertTrue(cfg["scale_xml_size"])

    def test_overrides_and_type_coercion(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "danmaku.conf").write_text(
                "enabled = false\nopacity = 0.5\nblock_top = true\n"
                "font_face = SimHei\ncache_limit = 50\n", encoding="utf-8")
            cfg = dm.load_config(Path(d))
            self.assertFalse(cfg["enabled"])
            self.assertEqual(cfg["opacity"], 0.5)
            self.assertTrue(cfg["block_top"])
            self.assertEqual(cfg["font_face"], "SimHei")
            self.assertEqual(cfg["cache_limit"], 50)

    def test_malformed_values_fall_back(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "danmaku.conf").write_text(
                "opacity = abc\nduration_marquee = xyz\n", encoding="utf-8")
            cfg = dm.load_config(Path(d))
            self.assertEqual(cfg["opacity"], 0.8)
            self.assertEqual(cfg["duration_marquee"], 12.0)

    def test_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = dm.load_config(Path(d))
            cfg["opacity"] = 0.33
            cfg["block_scroll"] = True
            self.assertTrue(dm.save_config(Path(d), cfg))
            back = dm.load_config(Path(d))
            self.assertEqual(back["opacity"], 0.33)
            self.assertTrue(back["block_scroll"])


class TestCache(unittest.TestCase):

    def test_ensure_ass_writes_and_caches(self):
        items = [{"start": 1.0, "mode": 1, "size": 25,
                  "color": 0xFFFFFF, "text": "hi"}]
        with tempfile.TemporaryDirectory() as d:
            p1 = dm.ensure_ass(111, 1920, 1080, config_dir=Path(d), items=items)
            self.assertIsNotNone(p1)
            self.assertTrue(p1.is_file())
            content1 = p1.read_text(encoding="utf-8")
            # 人为把 mtime 设老，再走一次：命中缓存不应重写内容
            os.utime(p1, (1000, 1000))
            p2 = dm.ensure_ass(111, 1920, 1080, config_dir=Path(d), items=items)
            self.assertEqual(p1, p2)
            self.assertEqual(p2.read_text(encoding="utf-8"), content1,
                             "命中缓存不应改变内容")
            # 复用时会 touch 以支持 LRU，因此 mtime 应当变新
            self.assertGreater(p2.stat().st_mtime, 1000)

    def test_cache_key_includes_resolution(self):
        items = [{"start": 1.0, "mode": 1, "size": 25, "color": 0xFFFFFF, "text": "x"}]
        with tempfile.TemporaryDirectory() as d:
            a = dm.ensure_ass(222, 1920, 1080, config_dir=Path(d), items=items)
            b = dm.ensure_ass(222, 1280, 720, config_dir=Path(d), items=items)
            self.assertNotEqual(a, b, "不同分辨率必须各自缓存")

    def test_no_items_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(dm.ensure_ass(333, 1920, 1080,
                                            config_dir=Path(d), items=[]))

    def test_no_bom_on_disk(self):
        items = [{"start": 1.0, "mode": 1, "size": 25, "color": 0xFFFFFF, "text": "中文"}]
        with tempfile.TemporaryDirectory() as d:
            p = dm.ensure_ass(444, 1920, 1080, config_dir=Path(d), items=items)
            raw = p.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertIn("中文".encode("utf-8"), raw)

    def test_prune_cache_keeps_newest(self):
        with tempfile.TemporaryDirectory() as d:
            cdir = dm.cache_dir_for(Path(d))
            cdir.mkdir(parents=True, exist_ok=True)
            for i in range(10):
                p = cdir / f"{i}.ass"
                p.write_text("x", encoding="utf-8")
                os.utime(p, (1000 + i, 1000 + i))
            removed = dm.prune_cache(Path(d), limit=4)
            self.assertEqual(removed, 6)
            self.assertEqual(len([p for p in cdir.iterdir() if p.is_file()]), 4)

    def test_prune_cache_noop_when_under_limit(self):
        with tempfile.TemporaryDirectory() as d:
            cdir = dm.cache_dir_for(Path(d))
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "a.ass").write_text("x", encoding="utf-8")
            self.assertEqual(dm.prune_cache(Path(d), limit=10), 0)

    def test_prune_missing_dir_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(dm.prune_cache(Path(d) / "nope", limit=1), 0)


@unittest.skipUnless(_MPV and _PIL, "需要 mpv 与 Pillow，跳过像素级渲染校验")
class TestDanmakuRendersInMpv(unittest.TestCase):
    """用真实 mpv 渲染生成的 ASS，断言弹幕像素真的出现。

    这是唯一能抓住「条件恒假/画错位置」这类静默失败的检查。
    """

    W, H = 960, 540

    def _render(self, ass_text, tmpdir, delay: float = 1.5):
        from PIL import Image
        ass = Path(tmpdir) / "dm.ass"
        ass.write_text(ass_text, encoding="utf-8")
        shot = str(Path(tmpdir) / "shot.png")
        helper = Path(tmpdir) / "h.lua"
        helper.write_text(
            'local mp=require"mp"\n'
            'mp.add_timeout(%r,function() mp.commandv("screenshot-to-file", %r, "window") end)\n'
            % (delay, shot), encoding="utf-8")
        proc = subprocess.Popen(
            [_MPV, f"av://lavfi:color=c=black:s={self.W}x{self.H}:d=120:r=5",
             "--no-config", "--vo=gpu-next", "--ao=null", "--force-window=yes",
             # 不用 --pause：滚动弹幕需要时间轴推进才会移动到画面中部
             "--geometry=%dx%d+60+60" % (self.W, self.H),
             # osd-level=0 关闭播放状态文字，否则左上角时间码会被算进亮像素，
             # 导致「空弹幕」也判定为有内容
             "--osd-level=0", "--no-osd-bar", "--osc=no",
             "--sub-file=" + str(ass), "--sid=1",
             "--script=" + str(helper),
             "--log-file=" + str(Path(tmpdir) / "mpv.log")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(delay + 3.0)
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        return shot

    @staticmethod
    def _bright_pixels(shot, w, h):
        from PIL import Image
        im = Image.open(shot).convert("RGB")
        return sum(1 for y in range(h) for x in range(0, w, 2)
                   if min(im.getpixel((x, y))) > 150)

    def test_danmaku_pixels_appear(self):
        """用「固定弹幕」做断言：滚动弹幕在截图时刻可能已移出视野，不稳定。"""
        items = [{"start": 0.0, "mode": 5, "size": 30, "color": 0xFFFFFF,
                  "text": f"FIXED-TOP-{n}"} for n in range(5)]
        items += [{"start": 0.0, "mode": 4, "size": 30, "color": 0xFFFFFF,
                   "text": f"FIXED-BOTTOM-{n}"} for n in range(3)]
        ass = dm.to_ass(items, self.W, self.H,
                        {"display_region": 1.0, "duration_marquee": 60.0,
                         "duration_still": 60.0, "opacity": 1.0})
        self.assertGreater(dm.count_events(ass), 0)
        with tempfile.TemporaryDirectory() as d:
            shot = self._render(ass, d)
            self.assertTrue(os.path.exists(shot), "未生成截图")
            bright = self._bright_pixels(shot, self.W, self.H)
            self.assertGreater(bright, 100,
                               f"弹幕未渲染出来（亮像素仅 {bright}）")

    def test_scrolling_danmaku_appears_mid_travel(self):
        """滚动弹幕取「已进入画面中部」的时刻截图，确保 \\move 真的生效。"""
        items = [{"start": 0.0, "mode": 1, "size": 30, "color": 0xFFFFFF,
                  "text": f"SCROLL-{n}"} for n in range(4)]
        ass = dm.to_ass(items, self.W, self.H,
                        {"display_region": 1.0, "duration_marquee": 60.0,
                         "duration_still": 60.0, "opacity": 1.0})
        self.assertIn("\\move(", ass)
        with tempfile.TemporaryDirectory() as d:
            shot = self._render(ass, d, delay=20.0)
            if not os.path.exists(shot):
                self.skipTest("no screenshot")
            bright = self._bright_pixels(shot, self.W, self.H)
            self.assertGreater(bright, 60,
                               f"滚动弹幕未渲染（亮像素仅 {bright}）")

    def test_empty_danmaku_renders_nothing(self):
        """空弹幕不应产生任何可见文字（避免误判成渲染成功）。"""
        ass = dm.to_ass([], self.W, self.H)
        with tempfile.TemporaryDirectory() as d:
            shot = self._render(ass, d)
            if not os.path.exists(shot):
                self.skipTest("no screenshot")
            bright = self._bright_pixels(shot, self.W, self.H)
            self.assertLess(bright, 200, f"空弹幕却渲染出 {bright} 个亮像素")


def _subtitle_mod():
    try:
        import subtitle
        return subtitle
    except Exception:
        return None


@unittest.skipUnless(_subtitle_mod(), "subtitle 模块不可用")
class TestSubtitle(unittest.TestCase):

    def setUp(self):
        self.st = _subtitle_mod()

    def test_pick_prefers_language_order(self):
        subs = [{"lan": "en", "url": "u1"}, {"lan": "ai-zh", "url": "u2"}]
        self.assertEqual(self.st.pick_subtitle(subs)["lan"], "ai-zh")

    def test_pick_respects_explicit_preference(self):
        subs = [{"lan": "ai-zh", "url": "u1"}, {"lan": "en", "url": "u2"}]
        self.assertEqual(self.st.pick_subtitle(subs, "en")["lan"], "en")

    def test_pick_empty(self):
        self.assertIsNone(self.st.pick_subtitle([]))
        self.assertIsNone(self.st.pick_subtitle(None))

    def test_unknown_lang_falls_back_to_first(self):
        subs = [{"lan": "xx", "url": "u"}]
        self.assertEqual(self.st.pick_subtitle(subs)["lan"], "xx")

    def test_cues_to_ass(self):
        cues = [{"start": 0.8, "end": 4.64, "text": "第一句"},
                {"start": 4.64, "end": 6.41, "text": "second"}]
        ass = self.st.cues_to_ass(cues, 1920, 1080)
        self.assertIn("[Script Info]", ass)
        self.assertIn("PlayResX: 1920", ass)
        self.assertEqual(ass.count("Dialogue:"), 2)
        self.assertIn("第一句", ass)
        self.assertNotIn("\ufeff", ass)

    def test_default_is_bottom_center(self):
        """默认必须底部居中（an=2）；这是用户明确要求的位置。"""
        ass = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}], 1920, 1080)
        style = [l for l in ass.splitlines() if l.startswith("Style: CC")][0]
        parts = style.split(",")
        self.assertEqual(parts[18], "2", f"Alignment 应为 2（底部居中），实际 {parts[18]}")

    def test_position_ratio_scales_with_height(self):
        """位置必须按比例，不能写死像素。

        真实 bug：早期实现把 MarginV 写死为 36px。
        在 4096x2048 上那只有 1.76% 高度，字幕几乎贴着底边；
        而在 1280x720 上却是 5%。现在按 height × position_ratio 计算。
        """
        for W, H in ((1920, 1080), (4096, 2048), (1280, 720), (3840, 2160)):
            with self.subTest(res=f"{W}x{H}"):
                ass = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}],
                                          W, H, cfg={"position_ratio": 0.06})
                parts = [l for l in ass.splitlines()
                         if l.startswith("Style: CC")][0].split(",")
                margin_v = int(parts[21])
                pct = margin_v / H
                self.assertAlmostEqual(pct, 0.06, delta=0.005,
                                       msg=f"{W}x{H} 下距底 {pct:.1%}，应为 6%")

    def test_font_size_scales_with_height(self):
        a = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}], 1920, 1080,
                                cfg={"font_size_ratio": 0.04})
        b = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}], 1920, 2160,
                                cfg={"font_size_ratio": 0.04})
        fa = int([l for l in a.splitlines() if l.startswith("Style: CC")][0].split(",")[2])
        fb = int([l for l in b.splitlines() if l.startswith("Style: CC")][0].split(",")[2])
        self.assertAlmostEqual(fb / fa, 2.0, delta=0.1)

    def test_all_alignments_map_to_valid_an(self):
        """九宫格每个选项都必须映射到合法的 ASS \an 值。"""
        for key, label in self.st.ALIGNMENTS.items():
            with self.subTest(alignment=key):
                ass = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}],
                                          1920, 1080, cfg={"alignment": key})
                an = int([l for l in ass.splitlines()
                          if l.startswith("Style: CC")][0].split(",")[18])
                self.assertIn(an, range(1, 10))
        # 底部居中必须是 2
        ass = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}],
                                  1920, 1080, cfg={"alignment": "bottom-center"})
        self.assertEqual([l for l in ass.splitlines()
                          if l.startswith("Style: CC")][0].split(",")[18], "2")

    def test_top_alignment_measures_from_top(self):
        ass = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}], 1920, 1080,
                                  cfg={"alignment": "top-center", "position_ratio": 0.05})
        parts = [l for l in ass.splitlines() if l.startswith("Style: CC")][0].split(",")
        self.assertEqual(parts[18], "8")
        self.assertAlmostEqual(int(parts[21]) / 1080, 0.05, delta=0.005)

    def test_border_style_box_vs_outline(self):
        box = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}], 1920, 1080,
                                  cfg={"border_style": "box"})
        out = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}], 1920, 1080,
                                  cfg={"border_style": "outline"})
        self.assertEqual([l for l in box.splitlines()
                          if l.startswith("Style: CC")][0].split(",")[15], "3")
        self.assertEqual([l for l in out.splitlines()
                          if l.startswith("Style: CC")][0].split(",")[15], "1")

    def test_color_is_bgr_and_configurable(self):
        ass = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}], 1920, 1080,
                                  cfg={"color": "#FF0000"})     # 纯红
        primary = [l for l in ass.splitlines()
                   if l.startswith("Style: CC")][0].split(",")[3]
        self.assertEqual(primary, "&H000000FF&", "ASS 是 BGR 序，纯红应为 &H000000FF&")

    def test_invalid_values_fall_back(self):
        ass = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}], 1920, 1080,
                                  cfg={"alignment": "nonsense", "color": "bogus",
                                       "position_ratio": "abc", "border_style": "weird"})
        parts = [l for l in ass.splitlines() if l.startswith("Style: CC")][0].split(",")
        self.assertEqual(parts[18], "2")      # 回退底部居中

    def test_position_ratio_clamped(self):
        ass = self.st.cues_to_ass([{"start": 0, "end": 1, "text": "x"}], 1920, 1080,
                                  cfg={"position_ratio": 99})
        margin_v = int([l for l in ass.splitlines()
                        if l.startswith("Style: CC")][0].split(",")[21])
        self.assertLessEqual(margin_v, 1080 * 0.41)

    def test_cues_sorted_by_start(self):
        cues = [{"start": 5.0, "end": 6.0, "text": "B"},
                {"start": 1.0, "end": 2.0, "text": "A"}]
        ass = self.st.cues_to_ass(cues)
        lines = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        self.assertIn("A", lines[0])
        self.assertIn("B", lines[1])

    def test_cues_to_ass_handles_garbage(self):
        self.assertEqual(self.st.cues_to_ass([]).count("Dialogue:"), 0)
        self.assertEqual(self.st.cues_to_ass(None).count("Dialogue:"), 0)

    def test_fetch_subtitles_no_session(self):
        self.assertEqual(self.st.fetch_subtitles(None, "BV1", 1), [])
        self.assertEqual(self.st.fetch_subtitles(object(), "", 1), [])

    def test_fetch_subtitles_handles_exception(self):
        class BadSess:
            def get(self, *a, **k):
                raise OSError("boom")
        self.assertEqual(self.st.fetch_subtitles(BadSess(), "BV1", 1), [])

    def test_fetch_subtitles_normalises_protocol_relative_url(self):
        class Resp:
            def json(self):
                return {"code": 0, "data": {"subtitle": {"subtitles": [
                    {"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//x/y.json"}]}}}
        class Sess:
            def get(self, *a, **k):
                return Resp()
        out = self.st.fetch_subtitles(Sess(), "BV1", 1)
        self.assertEqual(out[0]["url"], "https://x/y.json")

    def test_need_login_subtitle_is_skipped(self):
        class Resp:
            def json(self):
                return {"code": 0, "data": {"need_login_subtitle": True,
                                            "subtitle": {"subtitles": [
                                                {"lan": "ai-zh", "subtitle_url": "//x"}]}}}
        class Sess:
            def get(self, *a, **k):
                return Resp()
        self.assertEqual(self.st.fetch_subtitles(Sess(), "BV1", 1), [])

    def test_subtitle_config_is_separate_from_danmaku(self):
        """字幕配置必须独立成文件。

        两者默认值不同：弹幕 font_size_ratio=0.025 / opacity=0.8，
        字幕 font_size_ratio=0.045 / opacity=1.0。
        共用一份配置会让字幕继承弹幕的小字号与半透明。
        """
        import danmaku as dm
        self.assertNotEqual(self.st.DEFAULT_CONFIG["font_size_ratio"],
                            dm.DEFAULT_CONFIG["font_size_ratio"])
        self.assertNotEqual(self.st.DEFAULT_CONFIG["opacity"],
                            dm.DEFAULT_CONFIG["opacity"])

    def test_subtitle_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.st.load_config(Path(d))
            self.assertTrue((Path(d) / "subtitle.conf").exists())
            self.assertEqual(cfg["alignment"], "bottom-center")
            cfg["alignment"] = "top-center"
            cfg["position_ratio"] = 0.1
            cfg["color"] = "#FFFF00"
            self.assertTrue(self.st.save_config(Path(d), cfg))
            back = self.st.load_config(Path(d))
            self.assertEqual(back["alignment"], "top-center")
            self.assertEqual(back["position_ratio"], 0.1)
            self.assertEqual(back["color"], "#FFFF00")

    def test_invalid_alignment_in_config_is_corrected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "subtitle.conf").write_text("alignment = nonsense\n",
                                                   encoding="utf-8")
            cfg = self.st.load_config(Path(d))
            self.assertEqual(cfg["alignment"], "bottom-center")


@unittest.skipUnless(_MPV and _PIL, "需要 mpv 与 Pillow，跳过像素级渲染校验")
class TestSecondarySubtitleStyling(unittest.TestCase):
    """次字幕轨的 ASS 样式必须被保留。

    真实 bug：mpv 的 `secondary-sub-ass-override` 默认是 **strip**，
    会把次轨（本项目里的原生字幕）的 ASS 样式整段丢弃 ——
    实测表现为字幕变回默认小号白字并跑到画面顶部。
    只在 `--sub-ass-override` 上设 no 是不够的，那只管主轨（弹幕）。
    """

    W, H = 1280, 720

    def _mk(self, path, color, marginv, an, size, text, border=3):
        Path(path).write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {self.W}
PlayResY: {self.H}
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: X,Arial,{size},{color},&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,{border},2,0,{an},40,40,{marginv},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:01:00.00,X,,0,0,0,,{text}
""", encoding="utf-8")

    def _run(self, tmpdir, extra):
        dmf = Path(tmpdir) / "dm.ass"
        ccf = Path(tmpdir) / "cc.ass"
        self._mk(dmf, "&H0000FF00&", 10, 7, 22, "DANMAKU")
        self._mk(ccf, "&H000000FF&", 58, 2, 40, "REAL-SUBTITLE")
        shot = str(Path(tmpdir) / "shot.png")
        helper = Path(tmpdir) / "h.lua"
        helper.write_text(
            'local mp=require"mp"\n'
            'mp.add_timeout(2.5,function() mp.commandv("screenshot-to-file", %r, "window") end)\n'
            % shot, encoding="utf-8")
        proc = subprocess.Popen(
            [_MPV, f"av://lavfi:color=c=0x101010:s={self.W}x{self.H}:d=60:r=5",
             "--no-config", "--vo=gpu-next", "--ao=null", "--force-window=yes",
             "--pause=yes", "--geometry=%dx%d+50+50" % (self.W, self.H),
             "--osd-level=0", "--osc=no", "--no-osd-bar",
             "--sub-file=" + str(dmf), "--sub-file=" + str(ccf),
             "--sid=1", "--secondary-sid=2",
             "--script=" + str(helper),
             "--log-file=" + str(Path(tmpdir) / "mpv.log")] + extra,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(5)
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        return shot

    @staticmethod
    def _red_rows(shot, W, H):
        from PIL import Image
        im = Image.open(shot).convert("RGB")
        rows = []
        for y in range(H):
            n = sum(1 for x in range(0, W, 2)
                    if (lambda p: p[0] > 110 and p[1] < 90 and p[2] < 90)(im.getpixel((x, y))))
            if n > 3:
                rows.append(y)
        return rows

    def test_default_strip_loses_the_style(self):
        """先证明问题真实存在：默认设置下红色字幕样式被剥掉。"""
        with tempfile.TemporaryDirectory() as d:
            shot = self._run(d, [])
            if not os.path.exists(shot):
                self.skipTest("no screenshot")
            self.assertEqual(self._red_rows(shot, self.W, self.H), [],
                             "默认设置下本应被 strip 掉样式（若已有红色说明 mpv 行为变了）")

    def test_override_no_keeps_style_and_position(self):
        """修复后：字幕保持自己的颜色，并落在底部。"""
        with tempfile.TemporaryDirectory() as d:
            shot = self._run(d, ["--secondary-sub-ass-override=no"])
            if not os.path.exists(shot):
                self.skipTest("no screenshot")
            rows = self._red_rows(shot, self.W, self.H)
            self.assertTrue(rows, "次轨样式仍被丢弃（红色字幕未出现）")
            # MarginV=58/720 = 8% -> 应在下半屏
            self.assertGreater(min(rows), self.H * 0.6,
                               f"字幕未落在底部，实际 y={min(rows)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
