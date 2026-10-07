"""Canary：Cookie 的值永远不能进日志。

交接文档里的硬约束。守法是往代码里灌一眼就能认出来的假值，然后要求：
它必须真的流过被测路径（否则断言是空的），且日志里一个字都不出现。

零依赖：不联网（_query_nav 被打桩）、不碰仓库里那份活的 cookies.txt、
不写真日志。tkinter / qrcode 用假模块顶掉，所以 CI 上不必装依赖也能跑。
"""
import importlib.util
import re
import sys
import tempfile
import types
from pathlib import Path

# _log() 里的 print 走 sys.stdout；GitHub 的 windows runner 默认不是 UTF-8，
# 中文日志会抛 UnicodeEncodeError。真程序打包后 console=False，print 是空操作，
# 所以这里只纠测试自己的输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

for stub in ("tkinter", "tkinter.ttk", "tkinter.messagebox", "qrcode"):
    if stub not in sys.modules:
        m = types.ModuleType(stub)
        m.__path__ = []          # 让 from X import Y 也能过
        sys.modules[stub] = m
        setattr(sys.modules.get(stub.rsplit(".", 1)[0], m),
                stub.rsplit(".", 1)[-1], m)

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cb_to_mpv_under_test",
                                              HERE / "cb_to_mpv.pyw")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

SEED = re.sub(r"\W", "", Path(tempfile.mkdtemp()).name)
C = {name: f"CANARY-{name}-{SEED}" for name in
     ("SESSDATA", "buvid3", "bili_jct")}


def sandbox():
    tmp = Path(tempfile.mkdtemp())
    mod.LOG_PATH = tmp / "cb_to_mpv.log"
    if mod._log_fh is not None:
        mod._log_fh.close()
    mod._log_fh = None
    ck = tmp / "cookies.txt"
    ck.write_text(
        "# Netscape HTTP Cookie File\n"
        + "\t".join([".bilibili.com", "TRUE", "/", "TRUE", "0", "SESSDATA", C["SESSDATA"]]) + "\n"
        + "\t".join([".bilibili.com", "TRUE", "/", "TRUE", "0", "buvid3", C["buvid3"]]) + "\n"
        + "\t".join([".bilibili.com", "TRUE", "/", "TRUE", "4", "sessexp", C["bili_jct"]]) + "\n"
        + "\t".join([".example.com", "TRUE", "/", "FALSE", "0", "noise", "should-not-be-read"]) + "\n",
        encoding="utf-8")
    return tmp, ck


def drive(ck):
    """跑所有会读 Cookie 内容的路径，返回 _query_nav 实际收到的 header。"""
    seen = []

    def fake_nav(cookie_header):
        seen.append(cookie_header)
        return {"code": 0, "data": {"isLogin": True, "mid": 386179215, "vipStatus": 0}}

    real_nav, mod._query_nav = mod._query_nav, fake_nav
    try:
        mod._probe_cookies_file(str(ck))
        pairs = [mod._set_cookie_tuple(f"{n}={C[n]}; Path=/; Max-Age=2592000")
                 for n in ("SESSDATA", "bili_jct")]
        n = mod._merge_write_cookies(ck, pairs)
        mod._probe_cookies_file(str(ck))
    finally:
        mod._query_nav = real_nav
    return seen, n


def log_text(tmp):
    p = tmp / "cb_to_mpv.log"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def check_no_canary(label, text):
    leaked = [v for v in C.values() if v in text]
    assert not leaked, f"{label}：日志里出现了 Cookie 值（{len(leaked)} 条）"


def main():
    tmp, ck = sandbox()
    seen, merged = drive(ck)

    # 1. 前提：canary 确实流过了代码 —— 否则第 2 步的断言毫无意义
    assert len(seen) == 2, f"_query_nav 被调用 {len(seen)} 次，应为 2 次"
    assert C["SESSDATA"] in seen[0], "假值没进到 nav 的 header，测试没真正跑起来"
    assert C["bili_jct"] not in seen[0], "过期条目不该被拼进 header"
    assert C["bili_jct"] in seen[1], "合并后 bili_jct 应作为新条目进入 header"
    assert merged == 5, f"合并后应 5 条（保留 buvid3/sessexp/noise + 覆盖 SESSDATA + 新增 bili_jct），实际 {merged}"
    assert C["SESSDATA"] in ck.read_text(encoding="utf-8"), "cookies.txt 本身必须带值"

    # 2. 断言：日志干净，且不是空日志
    text = log_text(tmp)
    assert text.strip(), "日志是空的 —— 被测路径根本没写日志，断言不成立"
    assert "mid=386179215" in text and "已登录" in text, "登录态探测结果没进日志"
    check_no_canary("正常路径", text)

    # 3. 自检牙齿：故意泄漏一次，测试必须抓到
    tmp2, ck2 = sandbox()
    leaky_nav = lambda h: (mod._log(f"[debug] header={h}"),
                           {"code": 0, "data": {"isLogin": True, "mid": 1}})[1]
    mod._query_nav = leaky_nav
    try:
        mod._probe_cookies_file(str(ck2))
    finally:
        del mod._query_nav
    assert any(v in log_text(tmp2) for v in C.values()), "牙齿失效：故意泄漏都没抓到"
    try:
        check_no_canary("故意泄漏", log_text(tmp2))
    except AssertionError:
        pass
    else:
        raise SystemExit("牙齿失效：泄漏未被判为失败")

    print(f"canary OK —— 日志 {len(text.splitlines())} 行，"
          f"3 个假值均未出现；泄漏自检已被抓到")


if __name__ == "__main__":
    main()
