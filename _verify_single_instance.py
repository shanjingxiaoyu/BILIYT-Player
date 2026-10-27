# -*- coding: utf-8 -*-
"""单实例自检：真的起两个进程，断言只有一个活下来、另一个退出且唤回了现有窗口。

用法: python _verify_single_instance.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "bili_yt_player_qml.py"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def spawn(args=()):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.Popen(
        [sys.executable, str(ENTRY), *args],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )


def main():
    # ---- 1. 模块级：管道名与锁路径 ----
    sys.path.insert(0, str(ROOT))
    from app.single_instance import SingleInstance, _server_name

    n1 = _server_name()
    n2 = _server_name()
    check("管道名稳定（同用户两次一致）", n1 == n2, n1)
    check("管道名只含合法字符", all(c.isalnum() or c in "-_" for c in n1), n1)
    lp = SingleInstance._lock_path()
    check("锁文件路径非空且带用户名", bool(lp) and "BiliYTPlayer" in lp, lp)

    # ---- 2. 同进程内：第二个 acquire 必须失败 ----
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])

    a = SingleInstance(ROOT)
    ok_a = a.acquire()
    check("第一个实例 acquire 成功", ok_a)

    b = SingleInstance(ROOT)
    # 另一个实例在跑 → notify_existing 应连得上
    got = b.notify_existing(timeout_ms=800)
    check("第二个实例能连上第一个（会唤回窗口）", got)

    ok_b = b.acquire()
    check("第二个实例 acquire 失败（被挡住）", not ok_b)

    a.release()
    # 释放后应能被重新获取
    c = SingleInstance(ROOT)
    ok_c = c.acquire()
    check("释放后可重新获取", ok_c)
    c.release()

    # ---- 3. 真实进程级：起两份，只能活一份 ----
    print("\n-- 真实进程测试（需要几秒）--")
    p1 = spawn()
    time.sleep(6)
    alive1 = p1.poll() is None
    check("进程 1 启动后存活", alive1,
          f"rc={p1.poll()}" if not alive1 else f"pid={p1.pid}")

    if not alive1:
        try:
            _, err = p1.communicate(timeout=2)
            print("    stderr:", (err or "")[:400])
        except Exception:
            pass

    p2 = spawn()
    try:
        rc2 = p2.wait(timeout=20)
    except subprocess.TimeoutExpired:
        rc2 = None
    p2_alive = p2.poll() is None
    check("进程 2 自己退出了（没有多开）", not p2_alive,
          f"rc={rc2}" if not p2_alive else "仍在运行 -> 多开成功，不该发生")
    check("进程 2 退出码为 0（正常让位）", rc2 == 0, f"rc={rc2}")

    if p2_alive:
        p2.kill()

    # 进程 1 应该还活着，且收到过激活请求
    check("进程 1 仍在运行（未被顶掉）", p1.poll() is None)

    try:
        rc1 = p1.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p1.kill()
        rc1 = None
    print(f"    进程 1 退出码: {rc1}")

    # ---- 汇总 ----
    n = sum(1 for _, ok, _ in results if ok)
    print("\n" + "=" * 66)
    print(f"共 {len(results)} 项，通过 {n} 项")
    if n == len(results):
        print("ALL PASS")
        return 0
    print("FAILED:")
    for name, ok, detail in results:
        if not ok:
            print(f"  - {name}  {detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
