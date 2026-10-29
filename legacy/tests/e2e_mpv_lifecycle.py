# mpv 生命周期 E2E 回归测试（2026-10-30 首建，B-4 修复时）
# ============================================================================
# 验证「旧 mpv 清退 → 新管道建立」这条链，以及强杀兜底不会误伤。
#
# 用法（需要 mpv，会用 lavfi 测试源，不联网）：
#   .venv-tk/Scripts/python.exe tests/e2e_mpv_lifecycle.py
#
# ⚠️ 会 taskkill 清掉系统里所有 mpv.exe（含你正在看的那个），别边看片边跑。
#    跑完大约 1~3 分钟，期间会弹几次 mpv 窗口。
#
# 验证矩阵：
#   场景1  首次起播            → 管道建立、无残留告警
#   场景2  再起一次（换链接）  → 旧 mpv 被清退不双开、新管道建立
#   场景3  IPC 命令可达        → 切集/连播的前提
#   场景4  能定位本软件 mpv 的 PID
#   场景5  mpv 卡死不响应 quit → 强杀生效、不假死、如实报告清理失败
#   场景6  同名独立 mpv        → 不在强杀名单、不被误杀（安全绳）
# ============================================================================
import os, sys, time, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)      # tests/ 的上一级 = legacy/
sys.path.insert(0, ROOT)
import bili_clipboard_dolby as D

results = []
def check(name, ok, detail):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


def mpv_count():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq mpv.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=5)
        return len([l for l in (out.stdout or "").splitlines() if "mpv.exe" in l])
    except Exception:
        return -1


def pipe_ok():
    h = D._pipe_open(500)
    if h:
        D._pipe_close(h)
        return True
    return False


subprocess.run(["taskkill", "/F", "/IM", "mpv.exe"], capture_output=True)
time.sleep(1)
player = D.find_player()
print(f"  播放器 = {player}\n", flush=True)

SRC = "av://lavfi:testsrc=duration=120"

# ---- 场景1：首次起播（无残留）----
log1 = []
t0 = time.time()
D.launch_player(player, SRC, "B4-TEST-1", log=log1.append)
dt1 = time.time() - t0
print(f"  场景1 日志: {[l.strip() for l in log1]}", flush=True)
check("首次起播管道建立", pipe_ok(), f"耗时{dt1:.1f}s")
check("首次起播无残留告警", not any("没能清退" in l for l in log1), f"{len(log1)} 条日志")

# ---- 场景2：再起一次（模拟用户换链接，旧 mpv 必须被清退）----
log2 = []
t0 = time.time()
D.launch_player(player, SRC, "B4-TEST-2", log=log2.append)
dt2 = time.time() - t0
print(f"  场景2 日志: {[l.strip() for l in log2]}", flush=True)
n = mpv_count()
check("旧 mpv 被清退（不双开）", n == 1, f"mpv 进程数={n}")
check("新管道建立", pipe_ok(), f"耗时{dt2:.1f}s")
check("未误报残留告警", not any("没能清退" in l for l in log2), f"{len(log2)} 条日志")

# ---- 场景3：管道可用时，切集链路真能通（IPC 发命令）----
ok = D._ipc_send_cmd(["get_property", "path"])
check("IPC 命令可达（切集/连播前提）", ok, f"_ipc_send_cmd={ok}")

# ---- 场景4：识别本软件 mpv 的 PID（强杀的安全绳）----
pids = D._our_mpv_pids()
check("能定位本软件 mpv 的 PID", len(pids) == 1, f"pids={pids}（当前 mpv 进程数={mpv_count()}）")

# ---- 场景5：mpv 卡死不响应 quit → 走强杀，且如实报告"没清干净" ----
# 造一个真 mpv 占住管道，再把 _pipe_open 卡成"永远显示被占"，模拟不响应 quit
D.launch_player(player, SRC, "B4-STUCK", log=lambda m: None)
time.sleep(1)
before = mpv_count()
real_open, real_close = D._pipe_open, D._pipe_close
D._pipe_open = lambda timeout_ms=3000: 9999   # 假句柄 = 管道永远"还在"
D._pipe_close = lambda h: None
t0 = time.time()
r = D._quit_previous_mpv(timeout=1.0)         # 内部超时走短，测试别等太久
dt = time.time() - t0
D._pipe_open, D._pipe_close = real_open, real_close
after = mpv_count()
print(f"  场景5: _quit_previous_mpv 返回={r} 耗时={dt:.1f}s mpv数 {before}→{after}", flush=True)
check("卡死时强杀生效（卡住的 mpv 没了）", after == 0, f"进程数 {before}→{after}")
check("卡死时不假死（流程有终点）", dt < 6, f"{dt:.1f}s")
check("如实报告清理失败（供上层告知用户）", r is False, f"返回={r}")

# ---- 场景6：路径不同的独立 mpv 不得被误杀（强杀的安全绳）----
# 关键：文件名必须也叫 mpv.exe，放在别的目录——否则进程枚举(IMAGENAME eq mpv.exe)
# 本身就把它过滤掉了，测试等于没验证到真正的保护逻辑（2026-10-30 首版踩过）。
import tempfile, pathlib
indep_dir = pathlib.Path(tempfile.mkdtemp(prefix="mpv_indep_"))
indep = indep_dir / "mpv.exe"        # 同名、异路径 = 用户自己装的 mpv
made = False
try:
    os.link(player, indep)            # 硬链接：瞬间完成，不占 120MB
    made = True
except Exception as e:
    print(f"  (硬链接失败，跳过场景6: {e})", flush=True)

if made:
    p_indep = subprocess.Popen([str(indep), "--idle", "--no-config"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(1.5)
    D.launch_player(player, SRC, "B4-MINE", log=lambda m: None)
    time.sleep(1.5)
    n_all = mpv_count()
    pids = D._our_mpv_pids()
    # 前提校验：两个进程都得叫 mpv.exe，否则这场景是假通过
    check("前提：系统里确实有两个 mpv.exe", n_all == 2, f"mpv 进程数={n_all}")
    check("独立 mpv 不在强杀名单里", p_indep.pid not in pids,
          f"强杀名单={pids} 独立mpv PID={p_indep.pid}")
    check("独立 mpv 确实还活着（没被顺手杀掉）", p_indep.poll() is None,
          f"poll={p_indep.poll()}")
    check("本软件 mpv 在强杀名单里（该杀的还得能杀）", len(pids) == 1, f"强杀名单={pids}")
    try:
        p_indep.terminate()
    except Exception:
        pass
    subprocess.run(["taskkill", "/F", "/IM", "mpv.exe"], capture_output=True)
    time.sleep(0.5)
    try:
        indep.unlink()
        indep_dir.rmdir()
    except Exception:
        pass

subprocess.run(["taskkill", "/F", "/IM", "mpv.exe"], capture_output=True)
print(f"\n===== B-4 实测：{sum(results)}/{len(results)} PASS =====", flush=True)
sys.exit(0 if all(results) else 1)
