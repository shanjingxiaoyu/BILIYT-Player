# -*- coding: utf-8 -*-
"""真正模拟打包环境：_MEIPASS/legacy/mpv_osc.py（模块 __file__ 也在 _MEIPASS 下）。

关键认知：打包后 mpv_osc.py 被 PyInstaller 解到 _MEIPASS/legacy/，
所以 Path(__file__).parent 就是 _MEIPASS/legacy —— candidates[0] 恰好正确。
源码运行时 __file__ 在仓库 legacy/，也正确。
真正会出问题的是「模块被打进 PYZ（__file__ 不可靠）」的布局，那种情况才需要 _MEIPASS 回退。

本脚本用 exec 在一个假的 _MEIPASS/legacy 目录里执行 mpv_osc 的定位逻辑，
模拟真实打包后的 __file__ 语义。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"E:\work\pythondata\newprogram\BILIYT-Player-main")
LEGACY = ROOT / "legacy"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


src = (LEGACY / "mpv_osc.py").read_text(encoding="utf-8")
lua_src = LEGACY / "osc_base.lua"

# ---- 场景 1：真实打包布局（模块解到 _MEIPASS/legacy/，lua 也在那里）----
meipass1 = Path(tempfile.mkdtemp(prefix="mp1_"))
(meipass1 / "legacy").mkdir()
shutil.copy2(lua_src, meipass1 / "legacy" / "osc_base.lua")
fake_mod = meipass1 / "legacy" / "mpv_osc.py"
fake_mod.write_text(src, encoding="utf-8")

sys._MEIPASS = str(meipass1)
ns = {"__file__": str(fake_mod), "__name__": "mpv_osc_fake1"}
exec(compile(src, str(fake_mod), "exec"), ns)
p1 = ns["BASE_OSC_PATH"]
check("打包布局（模块在 _MEIPASS/legacy/）：找到 lua", p1.is_file(), str(p1))
check("  指向 _MEIPASS 下的那份", os.path.realpath(meipass1) in os.path.realpath(str(p1)), str(p1))

# ---- 场景 2：lua 只在 _MEIPASS 根（另一种 spec 写法）----
meipass2 = Path(tempfile.mkdtemp(prefix="mp2_"))
(meipass2 / "legacy").mkdir()
shutil.copy2(lua_src, meipass2 / "osc_base.lua")          # 只在根
fake_mod2 = meipass2 / "legacy" / "mpv_osc.py"            # 模块仍在 legacy/
fake_mod2.write_text(src, encoding="utf-8")

sys._MEIPASS = str(meipass2)
ns2 = {"__file__": str(fake_mod2), "__name__": "mpv_osc_fake2"}
exec(compile(src, str(fake_mod2), "exec"), ns2)
p2 = ns2["BASE_OSC_PATH"]
check("lua 只在 _MEIPASS 根：仍能找到", p2.is_file(), str(p2))

# ---- 场景 3：模块打进 PYZ（__file__ 不可靠），lua 在 _MEIPASS/legacy ----
meipass3 = Path(tempfile.mkdtemp(prefix="mp3_"))
(meipass3 / "legacy").mkdir()
shutil.copy2(lua_src, meipass3 / "legacy" / "osc_base.lua")
# 用一个不存在的幽灵 __file__ 模拟「模块在压缩包里」
ghost = meipass3 / "nonexistent" / "mpv_osc.py"
sys._MEIPASS = str(meipass3)
ns3 = {"__file__": str(ghost), "__name__": "mpv_osc_fake3"}
exec(compile(src, str(ghost), "exec"), ns3)
p3 = ns3["BASE_OSC_PATH"]
check("模块在 PYZ（__file__ 幽灵）：回退到 _MEIPASS 找到", p3.is_file(), str(p3))
check("  指向 _MEIPASS 下的那份", os.path.realpath(meipass3) in os.path.realpath(str(p3)), str(p3))

# ---- 场景 4：哪里都没有 ----
meipass4 = Path(tempfile.mkdtemp(prefix="mp4_"))
sys._MEIPASS = str(meipass4)
ghost4 = meipass4 / "nonexistent" / "mpv_osc.py"
ns4 = {"__file__": str(ghost4), "__name__": "mpv_osc_fake4"}
exec(compile(src, str(ghost4), "exec"), ns4)
p4 = ns4["BASE_OSC_PATH"]
check("全都不存在：不抛异常且返回 Path", isinstance(p4, Path), str(p4))

del sys._MEIPASS
for d in (meipass1, meipass2, meipass3, meipass4):
    shutil.rmtree(d, ignore_errors=True)

# ---- 场景 5：真实 exe 里文件在不在 ----
import subprocess
r = subprocess.run(
    [sys.executable, "-c",
     "from PyInstaller.archive.readers import CArchiveReader;"
     "arc=CArchiveReader(r'dist\\BiliYTPlayer.exe');"
     "t=arc.toc;"
     "ks=[n for n in (t.keys() if isinstance(t,dict) else [e[0] for e in t])];"
     "print([k for k in ks if 'osc_base' in k.lower()])"],
    cwd=str(ROOT), capture_output=True, text=True, timeout=90)
check("exe 资产里包含 osc_base.lua", "osc_base" in (r.stdout or ""),
      (r.stdout or "").strip()[:120])

n = sum(1 for _, o, _ in results if o)
print("\n" + "=" * 66)
print(f"共 {len(results)} 项，通过 {n} 项")
print("ALL PASS" if n == len(results) else "FAILED")
for name, o, d in results:
    if not o:
        print(f"  - {name}  {d}")
sys.exit(0 if n == len(results) else 1)
