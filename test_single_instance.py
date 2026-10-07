"""单实例：重复启动要弹一次框，且不再建主窗口。

打包后是 console=False，sys.stdout 为 None，main() 里那句 print 用户根本
看不见 —— 所以"已经有实例在跑"必须由 MessageBoxW 说出来。这条测试盯两件
事：框真的被弹了（一次，不是零次也不是两次），以及走这条路时不会有第二个
窗口、第二个监听线程。

零依赖：不弹真框（_user32 打桩）、不建真窗口（_Window 打桩）；互斥体那段
用真 kernel32，但换一个带随机段的测试专用名字，免得本机真的开着程序时误判。
"""
import importlib.util
import re
import sys
import tempfile
import types
from pathlib import Path

# windows runner 的 stdout 默认不是 UTF-8，中文 print 会抛 UnicodeEncodeError。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

for stub in ("tkinter", "tkinter.ttk", "tkinter.messagebox", "qrcode"):
    if stub not in sys.modules:
        m = types.ModuleType(stub)
        m.__path__ = []
        sys.modules[stub] = m
        setattr(sys.modules.get(stub.rsplit(".", 1)[0], m),
                stub.rsplit(".", 1)[-1], m)

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cb_to_mpv_si", HERE / "cb_to_mpv.pyw")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

SEED = re.sub(r"\W", "", Path(tempfile.mkdtemp()).name)

real_acquire = mod._acquire_single_instance
real_user32 = mod._user32
real_window = mod._Window


class Box:
    """记录 MessageBoxW 的调用，不真的弹出来。"""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a, k))
            return 0
        return rec


def boxes():
    return [c for c in box.calls if c[0] == "MessageBoxW"]


# ── A 真互斥体：同名第二次拿不到 ──────────────────────────────────────
mod._MUTEX_NAME = f"Local\\CB-to-MPV-test-{SEED}"
first = real_acquire()
assert first, "第一次就该拿到互斥体，否则测试环境本身不对"
assert real_acquire() is None, "同一名字第二次还拿到句柄，单实例根本没生效"
mod._kernel32.CloseHandle(first)
print(f"A 真互斥体 {mod._MUTEX_NAME}：首次拿到句柄，重复获取返回 None")

# ── B 提示框内容 ─────────────────────────────────────────────────────
box = Box()
mod._user32 = box
mod._notify_already_running()
assert len(boxes()) == 1, f"该弹一次，实际 {len(boxes())} 次"
_, args, _kw = boxes()[0][0], boxes()[0][1], boxes()[0][2]
hwnd, text, caption, flags = args
assert hwnd is None, "没有父窗口，应当传 None"
assert "已经在运行" in text, f"提示语没说明原因：{text!r}"
assert caption == "剪贴板直连播放器", f"标题栏不对：{caption!r}"
assert flags & 0x30 and flags & 0x00040000, f"缺少警告图标/置顶标志：{flags:#x}"
print(f"B 提示框：调用 1 次，flags={flags:#x}，caption={caption}")


class Win:
    """假主窗口：只记录有没有被建出来、mainloop 进没进。"""
    built = 0
    looped = 0

    def __init__(self):
        Win.built += 1
        self.root = self

    def mainloop(self):
        Win.looped += 1


threads = []
real_thread = mod.threading.Thread
FakeThread = type("T", (), {"start": lambda s: None})
mod.threading.Thread = lambda *a, **k: (threads.append(k.get("args") or a),
                                        FakeThread())[1]

# ── C 已有实例：弹框、不建窗口、不起线程 ──────────────────────────────
box.calls.clear(); Win.built = Win.looped = 0; threads.clear()
mod._acquire_single_instance = lambda: None
mod._Window = Win
mod.main()
assert len(boxes()) == 1, f"已有实例时该弹一次框，实际 {len(boxes())} 次"
assert Win.built == 0, "已有实例时不该建第二个主窗口"
assert Win.looped == 0 and not threads, "已有实例时不该进 mainloop、不该起监听线程"
print("C 重复启动：弹框 1 次，未建窗口、未起线程")

# ── D 首个实例：正常建窗，不弹框 ─────────────────────────────────────
box.calls.clear(); Win.built = Win.looped = 0; threads.clear()
mod._acquire_single_instance = lambda: "MOCK-HANDLE"
mod._Window = Win
mod.main()
assert not boxes(), f"正常启动不该弹框，实际 {len(boxes())} 次"
assert Win.built == 1 and Win.looped == 1, f"该建一次窗口并进 mainloop：{Win.built}/{Win.looped}"
print("D 首次启动：建窗 1 次、进 mainloop，未弹框")

# ── E 系统调用失败：不是"已有实例"，别谎报 ────────────────────────────
# CreateMutexW 返回 NULL 是环境问题，和"另一个实例占着"是两回事；
# main() 只把 None 当作重复启动，所以这条路径照常建窗、也不该弹框。
box.calls.clear(); Win.built = Win.looped = 0; threads.clear()
mod._acquire_single_instance = lambda: 0
mod.main()
assert not boxes(), "取不到互斥体时不该弹『已经在运行』"
assert Win.built == 1, f"互斥体失败时应照常启动：built={Win.built}"
print("E 互斥体创建失败：照常建窗、不弹提示框（与重复启动区分开）")

mod._acquire_single_instance, mod._user32, mod._Window = real_acquire, real_user32, real_window
mod.threading.Thread = real_thread
print("\nALL OK")
