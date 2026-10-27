#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sponsorblock 模块单元测试（标准库 unittest，无额外依赖）。

运行：
    python -m unittest discover -s legacy/tests -v
    python legacy/tests/test_sponsorblock.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 让测试能直接 import legacy/ 下的模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sponsorblock as sb  # noqa: E402


class TestFilterSegments(unittest.TestCase):
    """filter_segments 是安全关键路径：放过去一个 [0,0] 就会让视频跳回开头。"""

    def _seg(self, start, end, category="sponsor", action="skip", uuid="u"):
        return {"start": start, "end": end, "category": category,
                "actionType": action, "uuid": uuid}

    def test_drops_full_action_with_zero_range(self):
        """actionType=full 的整片推广标签是 segment=[0,0]，必须丢弃。"""
        segs = [self._seg(0, 0, "sponsor", "full")]
        self.assertEqual(sb.filter_segments(segs, ("sponsor",)), [])

    def test_drops_full_even_with_valid_range(self):
        """即使是 full 但区间合法也不能当跳过处理。"""
        segs = [self._seg(10, 60, "sponsor", "full")]
        self.assertEqual(sb.filter_segments(segs, ("sponsor",)), [])

    def test_drops_poi_action(self):
        """poi 是高光跳转，不是广告。"""
        segs = [self._seg(100, 100, "poi_highlight", "poi")]
        self.assertEqual(sb.filter_segments(segs, ("poi_highlight",)), [])

    def test_keeps_mute_action(self):
        segs = [self._seg(10, 20, "sponsor", "mute")]
        out = sb.filter_segments(segs, ("sponsor",))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["actionType"], "mute")

    def test_drops_non_positive_range(self):
        for start, end in [(10, 10), (20, 10), (5, 0)]:
            with self.subTest(start=start, end=end):
                self.assertEqual(
                    sb.filter_segments([self._seg(start, end)], ("sponsor",)), [])

    def test_drops_below_min_duration(self):
        segs = [self._seg(10, 10.4)]  # 0.4s < 默认 1.0s
        self.assertEqual(sb.filter_segments(segs, ("sponsor",)), [])
        # 放宽阈值后应保留
        self.assertEqual(len(sb.filter_segments(segs, ("sponsor",), min_duration=0.1)), 1)

    def test_category_whitelist(self):
        segs = [self._seg(10, 20, "sponsor"), self._seg(30, 40, "intro")]
        out = sb.filter_segments(segs, ("sponsor",))
        self.assertEqual([s["category"] for s in out], ["sponsor"])

    def test_empty_whitelist_keeps_all_actionable(self):
        segs = [self._seg(10, 20, "sponsor"), self._seg(30, 40, "intro")]
        self.assertEqual(len(sb.filter_segments(segs, ())), 2)

    def test_merges_overlapping(self):
        segs = [self._seg(10, 20), self._seg(15, 30)]
        out = sb.filter_segments(segs, ("sponsor",))
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["start"], out[0]["end"]), (10.0, 30.0))

    def test_merges_near_adjacent(self):
        """间隔 < 0.5s 视为连续，合并以免连续 seek 抖动。"""
        segs = [self._seg(10, 20), self._seg(20.3, 30)]
        out = sb.filter_segments(segs, ("sponsor",))
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["start"], out[0]["end"]), (10.0, 30.0))

    def test_keeps_distant_segments_separate(self):
        segs = [self._seg(10, 20), self._seg(25, 30)]
        out = sb.filter_segments(segs, ("sponsor",))
        self.assertEqual(len(out), 2)

    def test_sorts_by_start(self):
        segs = [self._seg(50, 60), self._seg(10, 20), self._seg(30, 40)]
        out = sb.filter_segments(segs, ("sponsor",))
        self.assertEqual([s["start"] for s in out], [10.0, 30.0, 50.0])

    def test_tolerates_malformed_input(self):
        segs = [None, "x", {}, {"start": "a", "end": "b"},
                {"start": 1, "end": 2, "category": "sponsor", "actionType": "skip"}]
        out = sb.filter_segments(segs, ("sponsor",))
        self.assertEqual(len(out), 1)

    def test_handles_none(self):
        self.assertEqual(sb.filter_segments(None, ("sponsor",)), [])


class TestBuildPayload(unittest.TestCase):

    def test_roundtrip(self):
        segs = [{"start": 10.5, "end": 20.25, "category": "sponsor",
                 "actionType": "skip", "uuid": "abc", "tier": "auto",
                 "color": "&H0000D400&"}]
        cfg = {"behavior": "skip", "notify": True, "debug": False}
        payload = json.loads(sb.build_payload("BV1xx", "123", segs, cfg))
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["bvid"], "BV1xx")
        self.assertEqual(payload["cid"], "123")
        self.assertEqual(len(payload["segments"]), 1)
        self.assertEqual(payload["segments"][0]["start"], 10.5)
        self.assertEqual(payload["segments"][0]["uuid"], "abc")
        # osc.lua 读 finish / color；跳过脚本读 end / tier
        self.assertEqual(payload["segments"][0]["finish"], 20.25)
        self.assertEqual(payload["segments"][0]["end"], 20.25)
        self.assertEqual(payload["segments"][0]["tier"], "auto")
        self.assertEqual(payload["segments"][0]["color"], "&H0000D400&")
        self.assertIn("undo_seconds", payload)

    def test_preserves_unicode_without_escaping(self):
        cfg = {"behavior": "skip", "notify": True, "debug": False}
        raw = sb.build_payload("BV1xx", "1", [], cfg, title="中文标题")
        self.assertIn("中文标题", raw)  # ensure_ascii=False
        self.assertEqual(json.loads(raw)["title"], "中文标题")

    def test_no_bom(self):
        """Lua 的 load() 不接受 BOM，载荷首字节绝不能是 EF。"""
        raw = sb.build_payload("BV1", "1", [], {})
        self.assertNotEqual(raw.encode("utf-8")[:1], b"\xef")

    def test_truncates_to_max(self):
        segs = [{"start": i, "end": i + 5, "category": "sponsor",
                 "actionType": "skip", "uuid": str(i)}
                for i in range(sb.MAX_SEGMENTS + 50)]
        payload = json.loads(sb.build_payload("BV1", "1", segs, {}))
        self.assertEqual(len(payload["segments"]), sb.MAX_SEGMENTS)

    def test_tolerates_missing_cfg_keys(self):
        payload = json.loads(sb.build_payload("BV1", None, [], {}))
        self.assertEqual(payload["behavior"], "skip")
        self.assertEqual(payload["cid"], "")


class TestEnsureLuaScript(unittest.TestCase):

    def test_writes_script_without_bom(self):
        with tempfile.TemporaryDirectory() as d:
            path = sb.ensure_lua_script(Path(d))
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "bsponsor.lua")
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "Lua 脚本不能有 BOM")
            self.assertEqual(raw.decode("utf-8"), sb.SKIP_SCRIPT_LUA)

    def test_does_not_rewrite_when_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            path = sb.ensure_lua_script(Path(d))
            first = path.stat().st_mtime_ns
            sb.ensure_lua_script(Path(d))
            self.assertEqual(path.stat().st_mtime_ns, first)

    def test_rewrites_when_content_differs(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / sb.SCRIPT_NAME
            path.write_text("-- stale", encoding="utf-8")
            sb.ensure_lua_script(Path(d))
            self.assertEqual(path.read_text(encoding="utf-8"), sb.SKIP_SCRIPT_LUA)

    def test_creates_missing_directory(self):
        with tempfile.TemporaryDirectory() as d:
            nested = Path(d) / "a" / "b"
            path = sb.ensure_lua_script(nested)
            self.assertTrue(path.exists())


class TestConfig(unittest.TestCase):

    def test_generates_template_and_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = sb.load_config(Path(d))
            self.assertTrue((Path(d) / "sponsorblock.conf").exists())
            self.assertTrue(cfg["enabled"])
            self.assertEqual(cfg["behavior"], "skip")
            self.assertEqual(cfg["timeout"], 2.5)
            self.assertFalse(cfg["report_views"])

    def test_reads_overrides_and_coerces_types(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "sponsorblock.conf").write_text(
                "enabled = false\nbehavior = mute\nmin_duration = 3\n"
                "timeout = 5.5\nnotify = false\nreport_views = true\n"
                "server = https://example.test\ncategories = intro,outro\n",
                encoding="utf-8")
            cfg = sb.load_config(Path(d))
            self.assertFalse(cfg["enabled"])
            self.assertEqual(cfg["behavior"], "mute")
            self.assertEqual(cfg["min_duration"], 3.0)
            self.assertEqual(cfg["timeout"], 5.5)
            self.assertFalse(cfg["notify"])
            self.assertTrue(cfg["report_views"])
            self.assertEqual(sb.categories_of(cfg), ("intro", "outro"))

    def test_ignores_comments_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "sponsorblock.conf").write_text(
                "# comment\n; other\n[bogus]\nunknown = 1\nenabled = false\n",
                encoding="utf-8")
            cfg = sb.load_config(Path(d))
            self.assertFalse(cfg["enabled"])
            self.assertNotIn("unknown", cfg)

    def test_malformed_values_fall_back(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "sponsorblock.conf").write_text(
                "min_duration = abc\ntimeout = xyz\n", encoding="utf-8")
            cfg = sb.load_config(Path(d))
            self.assertEqual(cfg["min_duration"], 1.0)
            self.assertEqual(cfg["timeout"], 2.5)

    def test_categories_of_filters_unknown(self):
        cfg = {"categories": "sponsor, bogus ,intro"}
        self.assertEqual(sb.categories_of(cfg), ("sponsor", "intro"))

    def test_categories_of_accepts_semicolons_and_lists(self):
        self.assertEqual(sb.categories_of({"categories": "sponsor;intro"}), ("sponsor", "intro"))
        self.assertEqual(sb.categories_of({"categories": ["sponsor", "intro"]}), ("sponsor", "intro"))

    def test_save_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = sb.load_config(Path(d))
            cfg["enabled"] = False
            cfg["behavior"] = "mute"
            self.assertTrue(sb.save_config(Path(d), cfg))
            reloaded = sb.load_config(Path(d))
            self.assertFalse(reloaded["enabled"])
            self.assertEqual(reloaded["behavior"], "mute")


class TestFetchSegments(unittest.TestCase):
    """网络层：无论发生什么都必须返回列表，绝不抛出。"""

    def _patch(self, **kwargs):
        return mock.patch.object(sb, "_http_get_json", **kwargs)

    def test_parses_valid_response(self):
        raw = [{"segment": [10.5, 20.0], "cid": "1", "UUID": "abc",
                "category": "sponsor", "actionType": "skip",
                "locked": 0, "votes": 3, "videoDuration": 300}]
        with self._patch(return_value=raw):
            out = sb.fetch_segments("BV1xx", "1")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["start"], 10.5)
        self.assertEqual(out[0]["end"], 20.0)
        self.assertEqual(out[0]["uuid"], "abc")

    def test_empty_list_is_not_an_error(self):
        """实测：无数据时代码是 200 + []，不是文档写的 404。"""
        with self._patch(return_value=[]):
            self.assertEqual(sb.fetch_segments("BV1xx", "1"), [])

    def test_timeout_returns_empty(self):
        import requests
        with self._patch(side_effect=requests.exceptions.Timeout("boom")):
            self.assertEqual(sb.fetch_segments("BV1xx", "1"), [])

    def test_connection_error_returns_empty(self):
        with self._patch(side_effect=OSError("no network")):
            self.assertEqual(sb.fetch_segments("BV1xx", "1"), [])

    def test_non_list_response_returns_empty(self):
        for bad in [{"code": 0}, "text", 42, None]:
            with self.subTest(bad=bad), self._patch(return_value=bad):
                self.assertEqual(sb.fetch_segments("BV1xx", "1"), [])

    def test_invalid_json_returns_empty(self):
        with self._patch(side_effect=json.JSONDecodeError("x", "y", 0)):
            self.assertEqual(sb.fetch_segments("BV1xx", "1"), [])

    def test_skips_malformed_entries(self):
        raw = [{"segment": [1, 2], "category": "sponsor", "actionType": "skip"},
               {"segment": [3]},
               {"segment": "nope"},
               {"no_segment": True},
               None]
        with self._patch(return_value=raw):
            out = sb.fetch_segments("BV1xx", "1")
        self.assertEqual(len(out), 1)

    def test_url_includes_cid_when_given(self):
        captured = {}

        def fake(url, timeout, proxy=None):
            captured["url"] = url
            return []

        with mock.patch.object(sb, "_http_get_json", side_effect=fake):
            sb.fetch_segments("BV1xx", "30719019937")
            self.assertIn("cid=30719019937", captured["url"])
            sb.fetch_segments("BV1xx", None)
            self.assertNotIn("cid=", captured["url"])

    def test_custom_server_used(self):
        captured = {}
        with mock.patch.object(sb, "_http_get_json",
                               side_effect=lambda url, t, proxy=None: captured.update(url=url) or []):
            sb.fetch_segments("BV1xx", "1", server="https://mirror.test/")
        self.assertTrue(captured["url"].startswith("https://mirror.test/api/skipSegments"))


class TestConstants(unittest.TestCase):

    def test_payload_env_name(self):
        self.assertEqual(sb.PAYLOAD_ENV, "BSPONSOR_PAYLOAD")

    def test_default_auto_categories_match_original_plugin(self):
        """默认自动跳过档位对齐原作者插件：宣传/自我推广/填充/非音乐/片尾填充。"""
        auto = {c for c, t in sb.CATEGORY_TIERS.items() if t == "auto"}
        self.assertEqual(auto, {"sponsor", "selfpromo", "filler",
                                "music_offtopic", "padding"})

    def test_default_tiers_cover_every_category(self):
        """每个已知分类都必须有默认档位，否则 GUI 下拉框会空。"""
        for c in sb.ALL_CATEGORIES:
            with self.subTest(category=c):
                self.assertIn(c, sb.CATEGORY_TIERS)
                self.assertIn(sb.CATEGORY_TIERS[c], sb.TIERS)

    def test_every_category_has_name_color_and_desc(self):
        for c in sb.ALL_CATEGORIES:
            with self.subTest(category=c):
                self.assertIn(c, sb.CATEGORY_NAMES)
                self.assertIn(c, sb.CATEGORY_COLORS)
                self.assertIn(c, sb.CATEGORY_DESCS)

    def test_colors_are_valid_ass(self):
        """ASS 颜色为 &HAABBGGRR&（8 位十六进制，AA 为透明度）。

        mpv 的 osc.lua 用 6 位 BBGGRR（osc_color_convert 只取 sub(6,7)..sub(4,5)..sub(2,3)），
        但 libass 同样接受 8 位形式，AA=00 表示完全不透明 —— 这是标准 ASS 写法。
        """
        import re
        pat = re.compile(r"^&H[0-9A-Fa-f]{8}&$")
        for c, col in sb.CATEGORY_COLORS.items():
            with self.subTest(category=c):
                self.assertRegex(col, pat)

    def test_all_category_colors_are_distinct(self):
        """颜色需可区分，否则进度条上看不出类别差异。"""
        vals = list(sb.CATEGORY_COLORS.values())
        dupes = {v for v in vals if vals.count(v) > 1}
        self.assertEqual(dupes, set(), f"重复配色: {dupes}")


class TestTierParsing(unittest.TestCase):
    """分类档位：新格式 cat:tier、旧格式裸名、非法输入的容错。"""

    def test_parses_new_format(self):
        out = sb.parse_categories("sponsor:auto, intro:manual, preview:bar, filler:prohibited")
        self.assertEqual(out, {"sponsor": "auto", "intro": "manual",
                               "preview": "bar", "filler": "prohibited"})

    def test_legacy_bare_names_become_auto(self):
        """向后兼容旧配置：裸分类名视为自动跳过。"""
        self.assertEqual(sb.parse_categories("sponsor,selfpromo"),
                         {"sponsor": "auto", "selfpromo": "auto"})

    def test_ignores_unknown_category_and_tier(self):
        self.assertEqual(sb.parse_categories("nope:auto, sponsor:bogus, intro:manual"),
                         {"intro": "manual"})

    def test_format_roundtrip_is_stable(self):
        tiers = dict(sb.CATEGORY_TIERS)
        self.assertEqual(sb.parse_categories(sb.format_categories(tiers)), tiers)

    def test_format_uses_fixed_order(self):
        s = sb.format_categories({"intro": "manual", "sponsor": "auto"})
        self.assertTrue(s.startswith("sponsor:auto"), s)
        self.assertIn("intro:manual", s)

    def test_active_categories_excludes_prohibited(self):
        tiers = {"sponsor": "auto", "intro": "manual",
                 "preview": "bar", "filler": "prohibited"}
        self.assertEqual(set(sb.active_categories(tiers)),
                         {"sponsor", "intro", "preview"})

    def test_categories_of_returns_actionable_only(self):
        cfg = {"category_tiers": {"sponsor": "auto", "intro": "manual",
                                  "preview": "bar", "filler": "prohibited"}}
        self.assertEqual(set(sb.categories_of(cfg)), {"sponsor", "intro"})


class TestTierFiltering(unittest.TestCase):
    """filter_segments 在不同档位下的取舍与配色。"""

    def _seg(self, start, end, cat="sponsor"):
        return {"start": start, "end": end, "category": cat,
                "actionType": "skip", "uuid": "u"}

    def test_attaches_tier_and_color(self):
        tiers = {"sponsor": "auto", "intro": "manual"}
        out = sb.filter_segments([self._seg(10, 20, "sponsor"),
                                  self._seg(30, 40, "intro")],
                                 ("sponsor", "intro"), tiers=tiers)
        self.assertEqual(out[0]["tier"], "auto")
        self.assertEqual(out[1]["tier"], "manual")
        self.assertEqual(out[0]["color"], sb.CATEGORY_COLORS["sponsor"])

    def test_prohibited_is_dropped(self):
        tiers = {"sponsor": "prohibited"}
        self.assertEqual(
            sb.filter_segments([self._seg(10, 20)], ("sponsor",), tiers=tiers), [])

    def test_bar_tier_is_kept_for_progressbar(self):
        """bar 档位不跳过但仍需返回，供进度条着色。"""
        tiers = {"preview": "bar"}
        out = sb.filter_segments([self._seg(10, 20, "preview")],
                                 ("preview",), tiers=tiers)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tier"], "bar")

    def test_does_not_merge_across_tiers(self):
        """不同档位必须保持独立，否则进度条会把两种颜色糊成一块。"""
        tiers = {"sponsor": "auto", "intro": "bar"}
        out = sb.filter_segments([self._seg(10, 20, "sponsor"),
                                  self._seg(20.2, 30, "intro")],
                                 ("sponsor", "intro"), tiers=tiers)
        self.assertEqual(len(out), 2, out)

    def test_merges_within_same_tier(self):
        tiers = {"sponsor": "auto"}
        out = sb.filter_segments([self._seg(10, 20, "sponsor"),
                                  self._seg(20.2, 30, "sponsor")],
                                 ("sponsor",), tiers=tiers)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["end"], 30.0)

    def test_unknown_category_defaults_to_auto(self):
        out = sb.filter_segments([self._seg(10, 20, "weird")], ("weird",))
        self.assertEqual(out[0]["tier"], "auto")


class TestBuildOscPayload(unittest.TestCase):

    def test_minimal_fields_for_osc(self):
        segs = [{"start": 10.0, "end": 20.0, "category": "sponsor",
                 "tier": "auto", "color": "&H0000D400&"}]
        p = json.loads(sb.build_osc_payload(segs))
        self.assertEqual(len(p["segments"]), 1)
        s = p["segments"][0]
        self.assertEqual(s["start"], 10.0)
        self.assertEqual(s["finish"], 20.0)   # osc.lua 用 finish
        self.assertEqual(s["color"], "&H0000D400&")
        self.assertNotIn("uuid", s)           # 画图不需要

    def test_color_falls_back_from_category(self):
        segs = [{"start": 1.0, "end": 5.0, "category": "intro"}]
        p = json.loads(sb.build_osc_payload(segs))
        self.assertEqual(p["segments"][0]["color"], sb.CATEGORY_COLORS["intro"])

    def test_handles_empty(self):
        self.assertEqual(json.loads(sb.build_osc_payload([]))["segments"], [])


class TestConfigTiers(unittest.TestCase):

    def test_load_attaches_category_tiers(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = sb.load_config(Path(d))
            self.assertEqual(cfg["category_tiers"], sb.CATEGORY_TIERS)

    def test_save_load_tier_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = sb.load_config(Path(d))
            cfg["category_tiers"] = {"sponsor": "manual", "intro": "prohibited"}
            self.assertTrue(sb.save_config(Path(d), cfg))
            back = sb.load_config(Path(d))
            self.assertEqual(back["category_tiers"],
                             {"sponsor": "manual", "intro": "prohibited"})

    def test_undo_seconds_default_and_override(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = sb.load_config(Path(d))
            self.assertEqual(cfg["undo_seconds"], 3.0)
            (Path(d) / "sponsorblock.conf").write_text(
                "undo_seconds = 7.5\n", encoding="utf-8")
            self.assertEqual(sb.load_config(Path(d))["undo_seconds"], 7.5)

    def test_empty_categories_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "sponsorblock.conf").write_text(
                "categories = totally,bogus\n", encoding="utf-8")
            cfg = sb.load_config(Path(d))
            self.assertEqual(cfg["category_tiers"], sb.CATEGORY_TIERS)


class TestLuaScriptStatic(unittest.TestCase):
    """对生成脚本做静态断言，防止关键逻辑被误删。"""

    def test_reads_payload_env(self):
        self.assertIn("BSPONSOR_PAYLOAD", sb.SKIP_SCRIPT_LUA)

    def test_uses_exact_absolute_seek(self):
        self.assertIn('"absolute+exact"', sb.SKIP_SCRIPT_LUA)

    def test_guards_against_full_and_poi(self):
        """Lua 侧也要挡一次 [0,0] 与非法动作，作为双保险。"""
        self.assertIn('b > a', sb.SKIP_SCRIPT_LUA)
        self.assertIn('action == "skip" or action == "mute"', sb.SKIP_SCRIPT_LUA)

    def test_exits_quietly_without_payload(self):
        self.assertIn('if not payload_raw or #payload_raw == 0 then', sb.SKIP_SCRIPT_LUA)

    @staticmethod
    def _lua_code_only(source: str) -> str:
        """去掉 Lua 行注释，只留可执行代码（避免注释里的示例文字误伤断言）。"""
        out = []
        for line in source.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("--"):
                continue
            out.append(line)
        return "\n".join(out)

    def test_no_reserved_word_field_access(self):
        """JSON 字段名 end 与 Lua 保留字冲突，只能写 s["end"]。

        这是一个真实发生过的 bug：静态子串断言全部通过，但 mpv 加载时报
        "'<name>' expected near 'end'"，跳过功能完全失效。
        `.end` 在任何 Lua 上下文里都是语法错误，故禁止出现在可执行代码中
        （注释里作为反例提到它是允许的）。
        """
        self.assertNotIn(".end", self._lua_code_only(sb.SKIP_SCRIPT_LUA))

    def test_payload_fields_use_bracket_indexing(self):
        """原始载荷字段（含保留字 end）必须用中括号取值。"""
        code = self._lua_code_only(sb.SKIP_SCRIPT_LUA)
        start = code.index("for _, s in ipairs(payload.segments")
        stop = code.index("table.sort(segments", start)
        block = code[start:stop]
        for field in ("start", "end", "category", "actionType", "uuid"):
            with self.subTest(field=field):
                self.assertIn(f's["{field}"]', block)

    def test_skip_happens_before_notification(self):
        """交互顺序：先跳过，再提示 + 倒计时（用户明确要求的顺序）。"""
        code = self._lua_code_only(sb.SKIP_SCRIPT_LUA)
        branch = code.index('elseif s.action == "skip"')
        seek_at = code.index('mp.commandv("seek", tostring(s.finish)', branch)
        osd_at = code.index("已跳过 %s（%d 秒内按 Enter 撤销）", branch)
        self.assertLess(seek_at, osd_at,
                        "必须先 seek 再提示，否则倒计时出现在跳过之前")

    def test_undo_uses_forced_key_binding(self):
        """Enter 撤销必须用 forced 绑定。

        实测：mpv 的 keypress 只派发 input.conf 里的绑定；
        add_key_binding(nil, name, ...) 需用户手动在 input.conf 绑定才生效；
        只有 add_forced_key_binding 会无条件覆盖（含内置 ENTER=playlist-next）。
        """
        self.assertIn('mp.add_forced_key_binding("ENTER", "undo-skip"', sb.SKIP_SCRIPT_LUA)

    def test_undo_seeks_back_to_original_position(self):
        code = self._lua_code_only(sb.SKIP_SCRIPT_LUA)
        self.assertIn("undo.from", code)
        start = code.index("local function do_undo()")
        block = code[start:start + 900]
        self.assertIn('mp.commandv("seek", tostring(back), "absolute+exact")', block)

    def test_undo_marks_segment_cancelled(self):
        """撤销后该片段不再自动跳过（用户明确表示要看）。"""
        self.assertIn("seg.cancelled = true", sb.SKIP_SCRIPT_LUA)
        self.assertIn("not s.cancelled", sb.SKIP_SCRIPT_LUA)

    def test_manual_tier_does_not_autoskip(self):
        code = self._lua_code_only(sb.SKIP_SCRIPT_LUA)
        self.assertIn('if s.tier == "manual" then', code)
        # manual 分支必须在 skip 分支之前，且自身不含 seek
        manual_at = code.index('if s.tier == "manual" then')
        skip_at = code.index('elseif s.action == "skip"')
        self.assertLess(manual_at, skip_at)
        self.assertNotIn("seek", code[manual_at:skip_at])

    def test_rejects_prohibited_and_bar_tiers(self):
        """跳过脚本只处理 auto/manual；bar 由 OSC 负责显示。"""
        self.assertIn('tier == "auto" or tier == "manual"', sb.SKIP_SCRIPT_LUA)


try:
    _MPV = None
    _le = Path(__file__).resolve().parent.parent.parent / "mpv-portable" / "mpv.exe"
    if _le.is_file():
        _MPV = str(_le)
except Exception:
    _MPV = None


@unittest.skipUnless(_MPV, "mpv-portable/mpv.exe 不存在，跳过 Lua 语法校验")
class TestLuaScriptParses(unittest.TestCase):
    """用真实 mpv 解析脚本。

    仅靠子串断言无法发现语法错误，必须让 mpv 真正加载一次。
    之前正是这种情况让 `s.end` 这个保留字错误溜过了全部静态测试。
    """

    def test_mpv_loads_script_without_lua_errors(self):
        import subprocess
        import time

        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "bsponsor.lua"
            with open(script, "w", encoding="utf-8", newline="") as f:
                f.write(sb.SKIP_SCRIPT_LUA)
            log = Path(d) / "mpv.log"
            # 清掉载荷：脚本会提前 return，语法错误依然会在日志里报出来
            env = {k: v for k, v in os.environ.items() if k != sb.PAYLOAD_ENV}
            proc = subprocess.Popen(
                [_MPV, "--no-config", "--vo=null", "--ao=null", "--idle=yes",
                 "--load-scripts=no", "--script=" + str(script),
                 "--msg-level=all=debug", "--log-file=" + str(log)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
            )
            try:
                time.sleep(3)
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=10)
            finally:
                if proc.poll() is None:
                    proc.kill()

            text = log.read_text(encoding="utf-8", errors="replace")
            problems = [l for l in text.splitlines()
                        if "Lua error" in l or "stack traceback" in l]
            self.assertEqual(problems, [], "mpv 报告了 Lua 错误:\n" + "\n".join(problems))
            self.assertIn("reading options for bsponsor", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
