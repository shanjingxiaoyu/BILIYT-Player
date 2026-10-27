# -*- coding: utf-8 -*-
"""在打包后的 exe 环境里验证：能否找到 osc_base.lua 并成功生成补丁版 OSC。

不真开 mpv，只调 ensure_managed_osc()，看返回的路径与状态文本。
用户报的就是这一步失败：「缺少基线 osc.lua（osc_base.lua），彩色进度条不可用」。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"E:\work\pythondata\newprogram\BILIYT-Player-main")
sys.path.insert(0, str(ROOT / "legacy"))
os.chdir(ROOT)

import mpv_osc

print("BASE_OSC_PATH =", mpv_osc.BASE_OSC_PATH)
print("  存在:", mpv_osc.BASE_OSC_PATH.is_file())

cfg = Path(tempfile.mkdtemp(prefix="osc_test_"))
msgs = []
path, status = mpv_osc.ensure_managed_osc(cfg, log=lambda m: msgs.append(m))
print("\nensure_managed_osc 返回：")
print("  path  =", path)
print("  status=", status)
for m in msgs:
    print("  log:", m)

ok = path is not None and Path(path).is_file()
print("\n" + "=" * 60)
if ok:
    size = Path(path).stat().st_size
    print(f"OK: 补丁版 OSC 已生成 ({size} bytes)")
    # 补丁是否真的打上了：检查关键标记
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    has_sb = "BSPONSOR" in text or "bsponsor" in text.lower() or "sponsor" in text.lower()
    print(f"  内容含 SponsorBlock 相关标记: {has_sb}")
    print(f"  文件行数: {len(text.splitlines())}")
else:
    print("FAIL: 未能生成补丁版 OSC —— 就是用户报的那个问题")
sys.exit(0 if ok else 1)
