# BiliYTPlayer 鼠标/键盘交互 E2E 回归测试
# ============================================================================
# 验证 click-handler.lua + input.conf 的交互行为（2026-10-27 首测 8/8 PASS）
#
# 用法：
#   .venv-tk/Scripts/python.exe tests/e2e_interaction.py              # lavfi 测试源（零依赖）
#   .venv-tk/Scripts/python.exe tests/e2e_interaction.py <视频路径>    # 指定本地视频
#
# 验证矩阵：
#   T1/T2 左键单击 = 暂停/播放（300ms 防抖）
#   T3    → 短按 = 快进 5s 且不双触发（暂停态测量，双触发会≈10s）
#   T4    → 长按 = 2.0x 倍速，松开恢复
#   T5    SPACE 快捷键回归（input.conf 默认行为不受影响）
#   T6    左键双击 = 全屏/退出
#
# 实现说明：
#   - 纯 IPC 驱动：mpv 的 keydown/keyup/keypress IPC 命令直接派发输入事件，
#     复杂绑定（RIGHT 的 down/up）也能模拟，无需窗口消息/抢焦点。
#   - 长连接管道：mpv 会给每个新连接推事件流，须按 request_id 过滤事件行。
#   - 跑前 taskkill 清场：孤儿 mpv 会占 IPC 管道导致串台（项目红线）。
# ============================================================================
import json, subprocess, time, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)   # tests/ 的上一级 = legacy/
MPV = os.path.join(ROOT, "mpv-portable", "mpv.exe")
CONF = os.path.join(ROOT, "mpv-portable", "portable_config")
VIDEO = sys.argv[1] if len(sys.argv) > 1 else "av://lavfi:testsrc=duration=600"
TITLE = "E2E-CLICK-TEST"
PIPE = f"\\\\.\\pipe\\mpv-e2e-{int(time.time())}"

subprocess.run(["taskkill", "/F", "/IM", "mpv.exe"], capture_output=True)
time.sleep(0.5)

CONN = None
def pipe_connect():
    global CONN
    for _ in range(60):
        try:
            CONN = open(PIPE, "r+b", buffering=0)
            return True
        except FileNotFoundError:
            time.sleep(0.25)
    return False

def pipe_cmd(*args, timeout=8):
    if CONN is None and not pipe_connect():
        return None
    CONN.write((json.dumps({"command": list(args), "request_id": 99}) + "\n").encode())
    t0 = time.time()
    while time.time() - t0 < timeout:
        line = CONN.readline()
        if not line:
            return None
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("request_id") == 99:
            return d
    return None

def prop(name): return (pipe_cmd("get_property", name) or {}).get("data")
def key(name, action): return pipe_cmd(action, name)

results = []
def check(name, ok, detail):
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)

proc = subprocess.Popen([MPV, f"--config-dir={CONF}", "--force-window=yes", "--no-terminal",
    f"--title={TITLE}", "--force-media-title=" + TITLE, "--geometry=880x495+300+250",
    f"--input-ipc-server={PIPE}", "--", VIDEO],
    creationflags=subprocess.CREATE_NO_WINDOW)
print(f"mpv PID={proc.pid}，等播放…", flush=True)
for i in range(60):
    pt = prop("playback-time")
    if (pt or 0) > 0.5: break
    time.sleep(0.5)
time.sleep(0.8)
print(f"起播完成 time={prop('playback-time')}", flush=True)

print("\n== T1/T2 左键单击 = 暂停/播放 ==", flush=True)
p0 = prop("pause"); key("MBTN_LEFT", "keypress"); time.sleep(0.7)
p1 = prop("pause")
check("单击暂停", p0 is False and p1 is True, f"pause {p0} -> {p1}")
key("MBTN_LEFT", "keypress"); time.sleep(0.7)
p2 = prop("pause")
check("再单击恢复", p2 is False, f"pause {p1} -> {p2}")

print("\n== T3 → 短按 = 快进 5s（暂停态消除自然播放噪声）==", flush=True)
# 测试陷阱：lavfi 测试源的 seek N 实际跳 2N（demuxer 怪癖，与交互无关），
# 「不双触发」的严格检测必须传真实视频；lavfi 模式下退化为「seek 生效」。
IS_LAVFI = "lavfi" in VIDEO
def probe_seek():
    pipe_cmd("set_property", "pause", "yes"); time.sleep(0.4)
    t0 = prop("playback-time"); key("RIGHT", "keydown"); time.sleep(0.08); key("RIGHT", "keyup")
    time.sleep(0.6); t1 = prop("playback-time")
    pipe_cmd("set_property", "pause", "no")
    return (t1 or 0) - (t0 or 0)

if IS_LAVFI:
    delta = probe_seek()
    check("短按seek生效", delta >= 3.0, f"lavfi态 Δ{delta:.3f}s（严格检测请传真实视频）")
else:
    delta = probe_seek()
    if delta < 0:
        # time 倒退 = 起播期 rebuffer 抖动（高码率大文件），重测一次
        time.sleep(2.0)
        delta2 = probe_seek()
        print(f"  （T3 首次 Δ{delta:.3f}s 疑 rebuffer，重测 Δ{delta2:.3f}s）", flush=True)
        delta = delta2 if delta2 >= 0 else delta
    check("短按快进=5s且不双触发", 3.0 <= delta <= 7.0, f"暂停态 Δ{delta:.3f}s（双触发会≈10s）")

print("\n== T4 → 长按 = 2.0x，松开恢复 ==", flush=True)
time.sleep(0.3)  # 等 T3 的 pause=no 切换落定，防 IPC 时序抖动
s0 = prop("speed"); key("RIGHT", "keydown"); time.sleep(0.8)
s_mid = prop("speed"); key("RIGHT", "keyup"); time.sleep(0.8)
s1 = prop("speed")
check("长按加速到2.0x", s_mid == 2.0, f"speed {s0} -> {s_mid}")
check("松开恢复1.0x", s1 == 1.0, f"speed {s_mid} -> {s1}")

print("\n== T5 回归：SPACE 快捷键 ==", flush=True)
p0 = prop("pause"); key("SPACE", "keypress"); time.sleep(0.5)
p1 = prop("pause")
check("SPACE 暂停", p0 != p1, f"pause {p0} -> {p1}")

print("\n== T6 左键双击 = 全屏 ==", flush=True)
f0 = prop("fullscreen")
key("MBTN_LEFT_DBL", "keypress"); time.sleep(1.0)
f1 = prop("fullscreen")
check("双击全屏", f0 is False and f1 is True, f"fullscreen {f0} -> {f1}")
key("MBTN_LEFT_DBL", "keypress"); time.sleep(1.0)
f2 = prop("fullscreen")
check("再双击退出", f2 is False, f"fullscreen {f1} -> {f2}")

print("\n== T7 真实双击序列（press1→dbl→press2，2026-10-27 用户实测bug回归）==", flush=True)
# 真实鼠标双击：SDL 把第二次点击转为 clicks=2 的 down/up，mpv 先派发 DBL、
# 后由 up 合成 press。若 press2 又起暂停定时器，300ms 后会误暂停（用户报告的现象）。
# 修复：lua 用 150ms 压制窗口（DBL_SUPPRESS）。注意 IPC 模拟的 dbl→press2 间隔会被
# 派发延迟拉到 ~70ms（实测 diff=0.069），阈值必须 > 此值；真实鼠标是亚毫秒级。
f0 = prop("fullscreen"); p0 = prop("pause")
key("MBTN_LEFT", "keypress"); time.sleep(0.1)      # press1
key("MBTN_LEFT_DBL", "keypress"); time.sleep(0.02)  # dbl
key("MBTN_LEFT", "keypress")                        # press2（双击余波）
time.sleep(0.8)                                      # 等过 300ms 防抖窗口
f1 = prop("fullscreen"); p1 = prop("pause")
check("真实双击只全屏不暂停", f1 is not f0 and p1 is p0,
      f"fullscreen {f0} -> {f1}（应切换）；pause {p0} -> {p1}（应不变）")
key("MBTN_LEFT_DBL", "keypress"); time.sleep(1.0)   # 还原窗口状态
f2 = prop("fullscreen")
check("T7后状态还原", f2 is f0, f"fullscreen {f1} -> {f2}")

try: pipe_cmd("quit")
except Exception: pass
time.sleep(0.5)
if proc.poll() is None: proc.kill()

fails = [r for r in results if not r[1]]
print(f"\n===== 汇总：{len(results)-len(fails)}/{len(results)} PASS =====", flush=True)
for n, ok, d in results: print(f"  [{'PASS' if ok else 'FAIL'}] {n} — {d}")
sys.exit(1 if fails else 0)
