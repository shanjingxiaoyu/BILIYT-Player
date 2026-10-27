#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mpv_osc 模块单元测试：补丁锚点、注入完整性、回退路径。

运行：
    python -m unittest discover -s legacy/tests -t .
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mpv_osc  # noqa: E402

try:
    import PIL  # noqa: F401
    _PIL = True
except ImportError:
    _PIL = False


def _mpv_exe():
    cand = Path(__file__).resolve().parent.parent.parent / "mpv-portable" / "mpv.exe"
    return str(cand) if cand.is_file() else None


_MPV = _mpv_exe()


class TestBaseOscPresent(unittest.TestCase):

    def test_base_osc_vendored(self):
        self.assertTrue(mpv_osc.BASE_OSC_PATH.is_file(),
                        f"缺少基线 osc.lua: {mpv_osc.BASE_OSC_PATH}")

    def test_base_is_mpv_osc(self):
        src = mpv_osc.BASE_OSC_PATH.read_text(encoding="utf-8")
        self.assertIn("osc_init", src)
        self.assertIn("render_elements", src)
        self.assertIn("new_element(\"tc_right\"", src)


class TestAnchors(unittest.TestCase):
    """锚点必须在基线中各自唯一，否则补丁会插错位置。"""

    @classmethod
    def setUpClass(cls):
        cls.base = mpv_osc.BASE_OSC_PATH.read_text(encoding="utf-8")

    def test_every_anchor_is_unique(self):
        for a in mpv_osc.ANCHORS:
            with self.subTest(anchor=a[:50]):
                self.assertEqual(self.base.count(a), 1,
                                 f"锚点出现 {self.base.count(a)} 次: {a[:60]}")

    def test_patch_report_lists_all_anchors(self):
        rep = mpv_osc.patch_report(self.base)
        self.assertEqual(set(rep["anchors"].values()), {1})

    def test_skipping_reports_lists_every_anchor(self):
        rep = mpv_osc.patch_report(self.base)
        self.assertEqual(set(rep["anchors"].values()), {1})


class TestBuildPatchedOsc(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.base = mpv_osc.BASE_OSC_PATH.read_text(encoding="utf-8")

    def test_patches_successfully(self):
        patched, ok, reason = mpv_osc.build_patched_osc(self.base)
        self.assertTrue(ok, reason)
        self.assertGreater(len(patched), len(self.base))
        self.assertTrue(mpv_osc.verify_patch(patched)[0])

    def test_all_markers_present_and_paired(self):
        patched, ok, _ = mpv_osc.build_patched_osc(self.base)
        self.assertTrue(ok)
        for m in mpv_osc.MARKERS:
            with self.subTest(marker=m):
                self.assertIn(">>> " + m, patched)
                self.assertIn("<<< " + m, patched)

    def test_no_bom_when_written(self):
        with tempfile.TemporaryDirectory() as d:
            p, st = mpv_osc.ensure_managed_osc(Path(d))
            self.assertIsNotNone(p, st)
            self.assertFalse(p.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_missing_anchor_fails_cleanly(self):
        """任一锚点消失都必须整体放弃，返回原始源码（回退内置 OSC）。"""
        for a in mpv_osc.ANCHORS:
            with self.subTest(anchor=a[:40]):
                broken = self.base.replace(a, "", 1)
                patched, ok, reason = mpv_osc.build_patched_osc(broken)
                self.assertFalse(ok)
                self.assertEqual(patched, broken)
                self.assertIn("锚点", reason)

    def test_duplicated_anchor_fails_cleanly(self):
        doubled = self.base + "\n" + mpv_osc.ANCHORS[0] + "\n"
        patched, ok, reason = mpv_osc.build_patched_osc(doubled)
        self.assertFalse(ok)
        self.assertIn("2 次", reason)

    def test_empty_source_fails_cleanly(self):
        patched, ok, reason = mpv_osc.build_patched_osc("")
        self.assertFalse(ok)
        self.assertEqual(patched, "")

    def test_verify_patch_rejects_unpaired_marker(self):
        patched, ok, _ = mpv_osc.build_patched_osc(self.base)
        self.assertTrue(ok)
        broken = patched.replace("<<< bsponsor: colored segment bars >>>", "", 1)
        ok2, reason2 = mpv_osc.verify_patch(broken)
        self.assertFalse(ok2)

    def test_verify_patch_rejects_missing_marker(self):
        ok, reason = mpv_osc.verify_patch("nothing here")
        self.assertFalse(ok)


class TestEnsureManagedOsc(unittest.TestCase):

    def test_writes_then_caches(self):
        with tempfile.TemporaryDirectory() as d:
            p1, s1 = mpv_osc.ensure_managed_osc(Path(d))
            self.assertEqual(s1, "written")
            mtime = p1.stat().st_mtime_ns
            p2, s2 = mpv_osc.ensure_managed_osc(Path(d))
            self.assertEqual(s2, "cached")
            self.assertEqual(p2.stat().st_mtime_ns, mtime)

    def test_rewrites_when_content_differs(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = mpv_osc.ensure_managed_osc(Path(d))
            p.write_text("-- stale", encoding="utf-8")
            _, s = mpv_osc.ensure_managed_osc(Path(d))
            self.assertEqual(s, "written")
            self.assertIn("bsponsor", p.read_text(encoding="utf-8"))

    def test_missing_base_returns_none(self):
        """基线缺失时必须回退（返回 None）而不是抛异常。"""
        orig = mpv_osc.BASE_OSC_PATH
        try:
            mpv_osc.BASE_OSC_PATH = Path("Z:/definitely/missing/osc.lua")
            with tempfile.TemporaryDirectory() as d:
                p, st = mpv_osc.ensure_managed_osc(Path(d))
                self.assertIsNone(p)
                self.assertEqual(st, "missing-base")
        finally:
            mpv_osc.BASE_OSC_PATH = orig

    def test_creates_missing_directory(self):
        with tempfile.TemporaryDirectory() as d:
            nested = Path(d) / "a" / "b"
            p, st = mpv_osc.ensure_managed_osc(nested)
            self.assertIsNotNone(p, st)
            self.assertTrue(p.is_file())


class TestPatchedOscContent(unittest.TestCase):
    """补丁内容本身的正确性（这些细节都有实测依据）。"""

    @classmethod
    def setUpClass(cls):
        base = mpv_osc.BASE_OSC_PATH.read_text(encoding="utf-8")
        cls.patched, cls.ok, _ = mpv_osc.build_patched_osc(base)

    def test_reads_payload_env(self):
        self.assertIn(mpv_osc.PAYLOAD_ENV, self.patched)

    def test_bar_uses_own_ass_object_for_positioning(self):
        """色块必须用独立 ass 对象并自带 \\pos。

        实测依据：mpv 的 player/lua/assdraw.lua:9 的 new_event() 只写一个换行，
        ASS 中换行即新事件行、不继承 \\pos，直接把图形画到无定位的角落
        （表现为跑到窗口左上角）。
        """
        i = self.patched.index("bsponsor: colored segment bars")
        block = self.patched[i:i + 1400]
        self.assertIn("assdraw.ass_new()", block)
        self.assertIn("o:pos(elem_geo.x, elem_geo.y)", block)
        self.assertIn("o:an(elem_geo.an)", block)
        self.assertIn("elem_ass:merge(o)", block)

    def test_bar_only_applies_to_seekbar(self):
        """判据必须是 element.type == "slider"。

        真实 bug：曾写成 element.name == "seekbar"，但 new_element() 从不设置
        name 字段，导致条件恒假、进度条上什么都不画（且无任何报错）。
        全文件只有一个 slider 元素，因此 type 是可靠且唯一的判据。
        """
        self.assertIn('if element.type == "slider" then', self.patched)
        self.assertNotIn("element.name ==", self.patched)

    def test_bar_skips_degenerate_ranges(self):
        self.assertIn("se > ss", self.patched)

    def test_adfree_time_replaces_tc_right_content(self):
        self.assertIn("bsponsor: ad-free duration", self.patched)
        self.assertIn("mp.format_time(bsponsor_state.adfree)", self.patched)

    def test_original_time_behaviour_preserved_as_fallback(self):
        """无片段时必须回退到 mpv 原有显示，不能显示空白。"""
        self.assertIn("state.rightTC_trem", self.patched)
        self.assertIn('mp.get_property_osd("duration")', self.patched)

    def test_right_trem_toggle_callback_intact(self):
        """区域替换不能吞掉 tc_right 的点击回调。"""
        self.assertEqual(
            self.patched.count("state.rightTC_trem = not state.rightTC_trem"), 1)

    def test_osc_loads_env_json(self):
        self.assertIn("parse_json", self.patched)
        self.assertIn("bsponsor_reload", self.patched)


@unittest.skipUnless(_MPV, "mpv-portable/mpv.exe 不存在，跳过真实解析校验")
class TestPatchedOscParsesInMpv(unittest.TestCase):
    """用真实 mpv 解析补丁版 OSC。

    仅靠子串断言无法发现语法错误（此前 `s.end` 就是被静态断言漏掉的），
    因此必须让 mpv 真正加载一次。
    """

    def _run_mpv(self, conf_dir, script):
        log = Path(conf_dir) / "mpv.log"
        env = {k: v for k, v in os.environ.items() if k != mpv_osc.PAYLOAD_ENV}
        proc = subprocess.Popen(
            [_MPV, "--config-dir=" + str(conf_dir), "--vo=null", "--ao=null",
             "--idle=yes", "--osc=no", "--script=" + str(script),
             "--msg-level=all=debug", "--log-file=" + str(log)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        try:
            time.sleep(4)
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        return log.read_text(encoding="utf-8", errors="replace")

    def test_no_lua_errors(self):
        with tempfile.TemporaryDirectory() as d:
            p, st = mpv_osc.ensure_managed_osc(Path(d))
            self.assertIsNotNone(p, st)
            text = self._run_mpv(d, p)
            problems = [l for l in text.splitlines()
                        if "Lua error" in l or "invalid escape" in l
                        or "stack traceback" in l]
            self.assertEqual(problems, [], "mpv 报告了 Lua 错误:\n" + "\n".join(problems))

    def test_osc_actually_executes(self):
        """证明补丁版 OSC 真的跑起来了，而不是只是「没报错」。

        判据：osc 初始化时会设置 user-data/osc/visibility 并注册鼠标区域
        （这些是它自己的副作用，不是 mpv 内置 OSC 产生的 —— 此处已 --osc=no）。
        """
        with tempfile.TemporaryDirectory() as d:
            p, _ = mpv_osc.ensure_managed_osc(Path(d))
            text = self._run_mpv(d, p)
            self.assertIn("user-data/osc/visibility", text)
            self.assertIn("osc_managed", text)


@unittest.skipUnless(_MPV and _PIL, "需要 mpv 与 Pillow，跳过像素级渲染校验")
class TestColoredBarsActuallyRender(unittest.TestCase):
    """像素级验证：进度条上真的出现了彩色区间。

    这是唯一能抓住「条件恒假/画错位置」这类静默失败的检查 ——
    它实际渲染并截图，然后断言目标行上出现预期颜色。
    曾有过 element.name 恒为 nil 导致什么都不画、且日志毫无报错的情况。
    """

    W, H = 800, 200
    VIDEO_SECONDS = 300

    def _render(self, segments, tmpdir):
        import sponsorblock as sb

        conf = Path(tmpdir) / "conf"
        (conf / "script-opts").mkdir(parents=True)
        # boxalpha=0 -> 不透明背景，便于识别像素
        (conf / "script-opts" / "osc.conf").write_text(
            "layout=bottombar\nvisibility=always\nhidetimeout=-1\n"
            "fadeduration=0\nboxalpha=0\n", encoding="utf-8")

        src = str(Path(tmpdir) / "black.mp4")
        subprocess.run(
            [_MPV, f"av://lavfi:color=c=black:s={self.W}x{self.H}:d={self.VIDEO_SECONDS}:r=2",
             "-o", src, "--no-config", "--ovc=libx264", "--frames=600", "--ao=null"],
            capture_output=True, timeout=300)

        payload = sb.build_payload("BV1test", "1", segments,
                                   {"behavior": "skip", "notify": True, "debug": False})
        osc, st = mpv_osc.ensure_managed_osc(conf)
        self.assertIsNotNone(osc, st)
        lua = conf / "bsponsor.lua"
        lua.write_text(sb.SKIP_SCRIPT_LUA, encoding="utf-8")

        shot = str(Path(tmpdir) / "shot.png")
        (conf / "shot.lua").write_text(
            'local mp=require"mp"\n'
            'mp.add_timeout(2.5,function() mp.commandv("screenshot-to-file", %r, "window") end)\n'
            % shot, encoding="utf-8")

        env = dict(os.environ, BSPONSOR_PAYLOAD=payload)
        proc = subprocess.Popen(
            [_MPV, src, "--config-dir=" + str(conf),
             "--vo=gpu-next", "--ao=null", "--keep-open=yes", "--force-window=yes",
             "--pause=yes", "--geometry=%dx%d+60+60" % (self.W, self.H),
             "--osd-level=3", "--osc=no",
             "--script=" + str(lua), "--script=" + str(osc),
             "--script=" + str(conf / "shot.lua"),
             "--msg-level=all=info", "--log-file=" + str(Path(tmpdir) / "mpv.log")],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(7)
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        return shot

    def _find_colored_rows(self, shot, predicate):
        from PIL import Image
        im = Image.open(shot).convert("RGB")
        w, h = im.size
        rows = {}
        for y in range(h):
            n = 0
            for x in range(0, w, 2):
                if predicate(im.getpixel((x, y))):
                    n += 1
            if n > 20:
                rows[y] = n
        return rows, im

    def test_green_sponsor_bar_renders(self):
        """纯绿区间必须真的画在进度条上。"""
        import sponsorblock as sb
        segs = [{"start": 30.0, "end": 90.0, "category": "sponsor",
                 "tier": "auto", "color": "&H0000FF00&", "actionType": "skip"}]
        with tempfile.TemporaryDirectory() as d:
            shot = self._render(segs, d)
            self.assertTrue(os.path.exists(shot), "未生成截图")
            rows, _ = self._find_colored_rows(
                shot, lambda p: p[1] > 110 and p[0] < 70 and p[2] < 70)
            self.assertTrue(rows, "进度条上未出现绿色区间（可能是选择器条件恒假）")

    def test_bar_width_matches_segment_proportion(self):
        """色块宽度应与区间时长占比一致，验证坐标换算正确。"""
        import sponsorblock as sb
        segs = [{"start": 150.0, "end": 300.0, "category": "sponsor",
                 "tier": "auto", "color": "&H0000FF00&", "actionType": "skip"}]
        with tempfile.TemporaryDirectory() as d:
            shot = self._render(segs, d)
            rows, im = self._find_colored_rows(
                shot, lambda p: p[1] > 110 and p[0] < 70 and p[2] < 70)
            self.assertTrue(rows, "未渲染出绿色区间")
            y = sorted(rows)[len(rows) // 2]
            xs = [x for x in range(im.size[0])
                  if (lambda p: p[1] > 110 and p[0] < 70 and p[2] < 70)(im.getpixel((x, y)))]
            self.assertTrue(xs, "该行没有绿色像素")
            width_frac = (max(xs) - min(xs)) / im.size[0]
            # 区间占 150/300 = 50% 时长；进度条本身约占窗口 75%，
            # 因此色块宽度应落在 30%~45% 之间
            self.assertGreater(width_frac, 0.28, f"色块过窄: {width_frac:.2f}")
            self.assertLess(width_frac, 0.48, f"色块过宽: {width_frac:.2f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
