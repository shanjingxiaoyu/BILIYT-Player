# B 站扫码登录链路 E2E 回归测试（2026-10-30 首建，B-2/B-3 修复时）
# ============================================================================
# 验证扫码窗的两个硬要求：
#   B-2 失败/取消必须给出原因 + 可重试，不再"程序一闪就没"
#   B-3 wait_window 期间任何情况都不许假死（子线程不许碰 tkinter）
#
# 用法（零网络、零依赖，随时可跑）：
#   .venv-tk/Scripts/python.exe tests/e2e_login.py
#
# 验证矩阵：
#   场景A  线程抛异常          → (None, "扫码过程异常: ...")
#   场景B  返回 None           → (None, "登录未完成")
#   场景B2 后端报了具体原因    → 原因要出现在弹窗正文里
#   场景C  成功                → ("FAKE_SD", None)，且二维码真的画在主线程
#   场景D  线程卡死 + 用户取消 → 1.5s 内强制收场（绝不假死）
#   场景E  冷启动失败 + 不重试 → SystemExit(0)
#   场景F  冷启动失败 → 重试 → 第二次成功 → 进入主流程
#
# 注意：
#   - 跑的过程中会在屏幕上短暂闪现扫码窗，属正常（窗口要真实存在才能验证绘制）。
#   - 真实登录态不会被动：冷启动场景把 .env 指向临时目录，测完即删。
#   - 不要和 e2e_interaction.py 混跑（后者会 taskkill 清场）。
# ============================================================================
import os, sys, time, threading
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)      # tests/ 的上一级 = legacy/
sys.path.insert(0, ROOT)
import tkinter as tk
import bili_clipboard_dolby as D
import bili_yt_player as G

results = []
def check(name, ok, detail):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)

QR_MATRIX = [[(x + y) % 3 == 0 for x in range(21)] for y in range(21)]  # 21x21 假二维码


def new_app():
    """造一个最小可用的 App（绕过完整 __init__，避免真扫码/真网络）。"""
    app = G.App.__new__(G.App)
    app.root = tk.Tk()
    app.root.withdraw()          # 隐藏主窗，只留二维码窗
    app.status_text = tk.Text(app.root, height=10, width=56)  # drain 会写主日志
    return app


def run_case(name, fake, expect, extra_pre=None, budget=10):
    app = new_app()
    D.qr_login_sessdata = fake
    if extra_pre:
        extra_pre(app)
    t0 = time.time()
    sd, err = app._qr_login_window()
    dt = time.time() - t0
    ok = expect(sd, err) and dt < budget
    check(name, ok, f"返回=({sd!r}, {err!r}) 耗时={dt:.1f}s（假死判据<{budget}s）")
    try:
        app.root.destroy()
    except Exception:
        pass
    return sd, err


print("场景A：线程内抛异常（B-3 假死病）", flush=True)
run_case("异常→关窗+error", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟网络炸了")),
         lambda sd, err: sd is None and err and "扫码过程异常" in err)

print("场景B：返回 None（二维码申请失败等，B-2 失败分类）", flush=True)
run_case("None→登录未完成", lambda *a, **k: None,
         lambda sd, err: sd is None and err == "登录未完成")

print("场景B2：后端报了具体原因（B-2 要求弹窗说清，不能只说「登录未完成」）", flush=True)
def fake_with_reason(*a, **k):
    k["emit"]("[*] 正在申请 B 站登录二维码…")
    k["emit"]("[!] 二维码申请失败: 模拟连接超时")
    return None
run_case("具体原因上弹窗", fake_with_reason,
         lambda sd, err: sd is None and err and "二维码申请失败" in err)

print("场景C：正常成功（回归 + 验证二维码确实画在主线程）", flush=True)
# 追踪 _draw_qr_canvas 的调用线程与绘制结果
draw_log = []
_real_draw = G.App._draw_qr_canvas
def spy_draw(canvas, matrix):
    _real_draw(canvas, matrix)
    try:
        draw_log.append((threading.current_thread().name, len(canvas.find_all())))
    except Exception as e:
        draw_log.append((threading.current_thread().name, f"ERR:{e}"))
G.App._draw_qr_canvas = staticmethod(spy_draw)

def fake_ok(*a, **k):
    k["draw_qr"](QR_MATRIX)
    k["emit"]("[*] 测试进度消息")
    time.sleep(0.2)   # 模拟扫码耗时，让 drain 至少跑一轮
    return "FAKE_SD"

run_case("成功→SESSDATA", fake_ok,
         lambda sd, err: sd == "FAKE_SD" and err is None)

# 子断言：绘制必须发生在主线程，且真的画出了矩形
d_thread = draw_log[0][0] if draw_log else "NONE"
d_count = draw_log[0][1] if draw_log else 0
check("二维码在主线程绘制", d_thread == "MainThread", f"绘制线程={d_thread}（子线程绘制必炸）")
check("canvas 真的有内容", isinstance(d_count, int) and d_count > 100,
      f"矩形数={d_count}（空白二维码则≈0）")
G.App._draw_qr_canvas = staticmethod(_real_draw)

print("场景D：线程卡死不响应 cancel（B-3 绝不假死 + B-2 取消可退出）", flush=True)
def click_cancel(app):
    """400ms 后点掉二维码窗的「取消」按钮（模拟用户不耐烦点取消）。
    注意：控件是 ttk.Button，winfo_class()=="TButton"，不是 tk.Button 的子类。"""
    def go():
        for w in app.root.winfo_children():
            if isinstance(w, tk.Toplevel):
                for c in w.winfo_children():
                    if c.winfo_class() == "TButton" and c.cget("text") == "取消":
                        print(f"    (模拟点击取消按钮 {c!r})", flush=True)
                        c.invoke()
                        return
                print("    (没找到取消按钮！)", flush=True)
    app.root.after(400, go)

run_case("卡死+取消→1.5s强制收场", lambda *a, **k: (time.sleep(60), None)[1],
         lambda sd, err: sd is None and err is None,
         extra_pre=click_cancel, budget=5)

# ---- 以下用真实 App 冷启动路径，验证 B-2 的「原因 + 重试」闭环 ----
import pathlib
_TMP_ENV = pathlib.Path(ROOT) / ".workbuddy" / "tmp_login_env" / ".env"
SHOWN = []          # 记录弹窗 (标题, 正文)
ANSWERS = []        # 用户对"是否重试"的回答队列
QR_RESULTS = []     # 每次扫码的结果队列


def _fake_ask(title, msg, **k):
    SHOWN.append((title, msg))
    return ANSWERS.pop(0) if ANSWERS else False


def cold_start(answers, qr_results):
    """构造真 App（冷启动：无 SESSDATA），走完整的 __init__ 扫码循环。"""
    _TMP_ENV.parent.mkdir(parents=True, exist_ok=True)
    if _TMP_ENV.exists():
        _TMP_ENV.unlink()
    G.ENV_PATH = _TMP_ENV
    D.ENV_PATH = _TMP_ENV
    SHOWN.clear(); ANSWERS[:] = answers; QR_RESULTS[:] = qr_results
    D.qr_login_sessdata = lambda *a, **k: (QR_RESULTS.pop(0) if QR_RESULTS else None)
    G.messagebox.askretrycancel = _fake_ask
    return G.App()


print("场景E：冷启动扫码失败 + 用户选择不重试（B-2 告别一闪而过）", flush=True)
t0 = time.time()
try:
    cold_start([False], [None])
    check("失败后应 SystemExit 而非假死", False, "没抛 SystemExit（说明卡在循环里了）")
except SystemExit as e:
    dt = time.time() - t0
    check("失败→弹原因→退出", e.code == 0 and len(SHOWN) == 1,
          f"SystemExit({e.code}) 弹窗次数={len(SHOWN)} 耗时={dt:.1f}s")
    if SHOWN:
        title, msg = SHOWN[0]
        check("弹窗说清了原因", "失败" in title and "登录未完成" in msg,
              f"标题={title!r} 正文={msg.splitlines()[0]!r}")

print("场景F：冷启动失败 → 用户重试 → 第二次成功（B-2 重试闭环）", flush=True)
t0 = time.time()
try:
    app = cold_start([True], [None, "FAKE_SD2"])
    dt = time.time() - t0
    check("重试后成功进入主流程", app.sessdata == "FAKE_SD2",
          f"sessdata={app.sessdata!r} 扫码次数={2} 耗时={dt:.1f}s")
    check("失败弹窗先出现过", len(SHOWN) == 1 and "失败" in SHOWN[0][0],
          f"弹窗={SHOWN[0][0]!r}" if SHOWN else "无弹窗")
    app.root.destroy()
except SystemExit as e:
    check("重试后成功进入主流程", False, f"不该退出，却 SystemExit({e.code})")

# 清理冷启动场景造的临时 .env，别把假的登录态留下
try:
    if _TMP_ENV.exists():
        _TMP_ENV.unlink()
    _TMP_ENV.parent.rmdir()
except Exception:
    pass

print("\n" + "=" * 46, flush=True)
fails = sum(1 for _, ok in results if not ok)
print(f"===== 汇总：{len(results)-fails}/{len(results)} PASS =====", flush=True)
sys.exit(1 if fails else 0)
