# -*- coding: utf-8 -*-
"""实测打包后的 exe：同时启动两次，只能有一个窗口、一个进程。"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import time
from pathlib import Path

EXE = Path(r"E:\work\pythondata\newprogram\BILIYT-Player-main\dist\BiliYTPlayer.exe")
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def bili_windows():
    out = []

    def _cb(h, _l):
        if user32.IsWindowVisible(h):
            n = user32.GetWindowTextLengthW(h)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(h, buf, n + 1)
                if "B站 / YouTube" in buf.value:
                    pid = wt.DWORD()
                    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                    out.append((h, buf.value, pid.value))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return out


def bili_processes():
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process | Where-Object {$_.ProcessName -eq 'BiliYTPlayer'} | "
         "Select-Object -ExpandProperty Id"],
        capture_output=True, text=True, timeout=30)
    return [int(x) for x in r.stdout.split() if x.strip().isdigit()]


def kill_all():
    for pid in bili_processes():
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, timeout=20)
    time.sleep(1.5)


# ---- 干净起步 ----
kill_all()
import os
lock = Path(os.environ["TEMP"]) / "BiliYTPlayer-Administrator.lock"
try:
    lock.unlink()
except OSError:
    pass

# ---- 1. 启动第一个 ----
p1 = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
time.sleep(12)
procs1 = bili_processes()
wins1 = bili_windows()
# 注意：PyInstaller 的 exe 会同时存在「引导进程 + 实际应用进程」，所以
# 单个实例本来就对应 2 个进程。判断多开要看「进程数有没有变多」以及
# 「主窗口数」，不能写死 == 1。
check("第一个实例启动，进程存在", len(procs1) >= 1, f"procs={procs1}")
check("第一个实例有且仅有 1 个主窗口", len(wins1) == 1, f"windows={len(wins1)}")
n_proc_after_first = len(procs1)

# ---- 2. 启动第二个 ----
p2 = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
time.sleep(10)
procs2 = bili_processes()
wins2 = bili_windows()
check("启动第二次后，进程数没有增加（没有多开）",
      len(procs2) == n_proc_after_first,
      f"之前 {n_proc_after_first} 个 -> 现在 {len(procs2)} 个")
check("启动第二次后，主窗口仍只有 1 个", len(wins2) == 1, f"windows={len(wins2)}")
check("第二个进程已自行退出", p2.poll() is not None, f"rc={p2.poll()}")
check("两次启动的窗口属于同一个进程（不是新开的）",
      bool(wins1) and bool(wins2) and wins1[0][2] == wins2[0][2],
      f"pid {wins1[0][2] if wins1 else '?'} vs {wins2[0][2] if wins2 else '?'}")

# ---- 3. 第一个还在 ----
check("第一个实例仍在运行（未被顶掉）", p1.poll() is None, f"rc={p1.poll()}")

# ---- 4. 关闭后能重新启动 ----
kill_all()
try:
    lock.unlink()
except OSError:
    pass
p3 = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
time.sleep(12)
procs3 = bili_processes()
check("退出后能重新启动（锁已释放）", len(procs3) >= 1, f"procs={procs3}")
kill_all()

n = sum(1 for _, o, _ in results if o)
print("\n" + "=" * 62)
print(f"共 {len(results)} 项，通过 {n} 项")
print("ALL PASS" if n == len(results) else "FAILED")
for name, o, d in results:
    if not o:
        print(f"  - {name}  {d}")
raise SystemExit(0 if n == len(results) else 1)
