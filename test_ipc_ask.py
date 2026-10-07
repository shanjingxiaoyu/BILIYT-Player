"""_ipc_ask：只认与自己 request_id 配对的那条回应，事件行一律不吃。

播放刚开始时 mpv 会往这条 IPC 连接上猛推事件（start-file / playback-restart /
audio-reconfig…）。实测 v0.41.0-1107：一次播放推来 6 条事件，全都不带
request_id —— 所以真正防止「把事件当回应」的是 rid 配对这一步；event 判断是
防呆，挡的是万一某版本把 rid 也写进事件。两条都盯着：A 用带 rid 的事件行试
event 判断，B 用不配对的回应试 rid 判断。回填失效是不报错的那种，只能靠测试。

零依赖：不连真 mpv、不开真管道。raw / rd 都是桩件，逐行喂预先写好的 JSON。
"""
import importlib.util
import json
import sys
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
spec = importlib.util.spec_from_file_location("cb_to_mpv_ipc", HERE / "cb_to_mpv.pyw")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class Raw:
    """IPC 写端：只记下写了什么字节。"""

    def __init__(self):
        self.buf = b""

    def write(self, data):
        self.buf += data

    def flush(self):
        pass

    def sent(self):
        return json.loads(self.buf.decode("utf-8").strip())


class Reader:
    """IPC 读端：按脚本逐行给；用完给空行（= 对端关了），repeat=True 时重复最后一行。"""

    def __init__(self, lines, repeat=False):
        self.lines = [l if isinstance(l, bytes) else l.encode("utf-8") for l in lines]
        self.repeat = repeat
        self.calls = 0

    def readline(self):
        self.calls += 1
        if self.lines:
            return self.lines.pop(0)
        if self.repeat:
            return b'{"event":"file-loaded"}\n'
        return b""


def ev(name, extra=None):
    d = {"event": name}
    if extra:
        d.update(extra)
    return json.dumps(d).encode("utf-8") + b"\n"


def reply(rid, data=None, error=None):
    d = {"request_id": rid, "error": error or "success"}
    if data is not None:
        d["data"] = data
    return json.dumps(d).encode("utf-8") + b"\n"


real_time = mod.time

try:
    # A. 事件行堆在回应前面：返回的必须是 rid 配对的那条回应，而且只发一条命令。
    #    实测 v0.41.0-1107 的 6 条事件行全都不带 request_id（本来也配不上 rid），
    #    所以这里刻意混进一条「带同一个 rid 的事件行」—— 那是 event 判断唯一能挡住的东西，
    #    少了它这条断言就变成对空气开火。
    raw, rd = Raw(), Reader([ev("start-file"), ev("playback-restart"),
                             json.dumps({"event": "audio-reconfig", "request_id": 7,
                                         "data": {"audio": "aac"}}).encode("utf-8") + b"\n",
                             reply(7, {"media-title": "某视频"})])
    got = mod._ipc_ask(raw, rd, "media-title", 7, real_time.time() + 10)
    assert isinstance(got, dict), f"A 拿到 {got!r}，事件行没被跳过"
    assert "event" not in got, f"A 把事件行当回应返回了：{got!r}"
    assert got["request_id"] == 7 and got["data"]["media-title"] == "某视频", f"A 返回错：{got!r}"
    assert raw.sent() == {"command": ["get_property", "media-title"], "request_id": 7}, \
        f"A 发出去的命令不对：{raw.buf!r}"
    assert rd.calls == 4, f"A 读了 {rd.calls} 行，预期 4（3 事件 + 1 回应）"
    print("A 事件行被跳过（包括一条带同 rid 的），拿到 rid=7 的回应；命令只发一条")

    # B. 别人的回应（rid 不配对）不能算自己的
    raw, rd = Raw(), Reader([reply(99, {"other": 1}), ev("clip-added"), reply(7, "avc1")])
    got = mod._ipc_ask(raw, rd, "video-codec", 7, real_time.time() + 10)
    assert got and got["request_id"] == 7, f"B 把别人的回应收走了：{got!r}"
    assert rd.calls == 3, f"B 读了 {rd.calls} 行，预期 3"
    print("B 忽略 request_id 不配对的回应，继续读到自己的")

    # C. 非 JSON 的行（管道上偶发的半行/噪声）不算回应也不该抛
    raw, rd = Raw(), Reader([b"\xff\xfe not json at all\n", b'{"data": {"x": 1}\n',
                             reply(3, {"audio-codec": "aac"})])
    got = mod._ipc_ask(raw, rd, "audio-codec", 3, real_time.time() + 10)
    assert got and got["request_id"] == 3, f"C 遇到坏行就崩或误判：{got!r}"
    print("C 坏 JSON 行被跳过，仍拿到正解")

    # D. 对端关闭：readline 给空字节 —— 立刻返回 None，不空转
    raw, rd = Raw(), Reader([ev("start-file")])
    got = mod._ipc_ask(raw, rd, "media-title", 5, real_time.time() + 10)
    assert got is None, f"D 管道已关还返回 {got!r}"
    assert rd.calls == 2, f"D 读次数 {rd.calls}，预期 2（1 事件 + 1 空行即止）"
    print("D 管道关闭时返回 None，且只读到那一行为止")

    # E. 只有事件、永无回应：必须靠 deadline 收场，而不是无限读
    class FakeTime:
        def __init__(self):
            self.t = 0.0

        def time(self):
            self.t += 1.0
            return self.t - 1.0

        def sleep(self, s):
            pass

    ft = FakeTime()
    mod.time = ft
    raw, rd = Raw(), Reader([ev("start-file")], repeat=True)
    got = mod._ipc_ask(raw, rd, "media-title", 9, 2.5)
    assert got is None, f"E 超时还返回东西：{got!r}"
    assert 2 <= rd.calls <= 4, f"E 循环了 {rd.calls} 次，deadline 没起作用"
    print(f"E 超时返回 None（deadline 内读了 {rd.calls} 行就收场）")
    mod.time = real_time          # 假时钟只服务 E，别让它漏到后面的分支

    # F. deadline 已经过期：一次都不该读（回填线程该收场时不能再敲管道）
    raw, rd = Raw(), Reader([reply(1, "x")], repeat=True)
    got = mod._ipc_ask(raw, rd, "media-title", 1, real_time.time() - 1)
    assert got is None and rd.calls == 0, f"F 过期后还读了 {rd.calls} 次"
    print("F deadline 已过时一次都不读")

    print("\nALL OK")
finally:
    mod.time = real_time
