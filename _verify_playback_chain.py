# -*- coding: utf-8 -*-
"""真实播放链路自检：不启动 mpv 窗口，只验证 bridge 能把 playurl → 挑流 → 
历史写入 → 弹幕/字幕准备 这条链走通。

用真实网络 + 真实后端，只把 launch_player 替换成记录器（避免真的弹 mpv 窗口）。

用法: python _verify_playback_chain.py [BV号]
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def main():
    app = QApplication(sys.argv[:1])
    from ui.bridge import UiBridge
    import ui.bridge as bmod
    import bili_clipboard_dolby as B

    bridge = UiBridge()
    logs = []
    bridge.logLine.connect(lambda s: (logs.append(s), print("   |", s)))

    # ---- 真实鉴权 ----
    from bili_clipboard_dolby import init_bili_session, find_player
    player = find_player()
    check("找到 mpv", bool(player), str(player))
    bridge.player_path = player
    try:
        bridge.session, bridge.img_key, bridge.sub_key = init_bili_session(bridge.sessdata)
        check("B 站鉴权成功", True)
    except Exception as e:
        check("B 站鉴权成功", False, str(e))
        return finish()

    bv = sys.argv[1] if len(sys.argv) > 1 else "BV1GJ411x7h7"

    # ---- 替换 launch_player 记录参数 ----
    captured = {}

    def fake_launch(player_path, video_url, title, audio_url=None, sessdata=None, log=None,
                    segments=None, sponsor_cfg=None, danmaku_ass=None, subtitle_file=None):
        captured.update(dict(player=player_path, vurl=video_url, title=title,
                             aurl=audio_url, segs=segments, dm=danmaku_ass, cc=subtitle_file))
        if log:
            log("    [+] mpv 正在播放: " + title)

    B.launch_player = fake_launch

    # ---- 全链路 ----
    t0 = time.time()
    bridge._play_bili(bv)
    dt = time.time() - t0

    check("链路走通（未抛异常）", bool(captured), f"{dt:.1f}s")
    check("拿到视频直链", bool(captured.get("vurl")) and "http" in str(captured.get("vurl")))
    check("拿到音频直链", bool(captured.get("aurl")) and "http" in str(captured.get("aurl")))
    check("标题非空", bool(captured.get("title")), str(captured.get("title"))[:40])
    check("播放入口是真实 mpv.exe",
          str(captured.get("player", "")).lower().endswith("mpv.exe"), str(captured.get("player")))

    # 历史（重播数据源）
    check("历史写入 1 条", len(bridge.history) == 1, str(len(bridge.history)))
    if bridge.history:
        h = bridge.history[0]
        check("历史含画质描述", "1080" in h["detail"] or "720" in h["detail"] or "P" in h["detail"],
              h["detail"][:50])
        check("历史 raw_id 可重播", h["raw_id"] == bv, h["raw_id"])
        check("历史 platform = B站", h["platform"] == "B站", h["platform"])

    # 日志应含关键节点
    joined = "\n".join(logs)
    check("日志含画质行", "[+] 画质:" in joined)
    check("日志含 SponsorBlock 结果", "SponsorBlock:" in joined)

    # ---- PGC（番剧）链路 ----
    print("\n-- PGC 番剧链路（此前 -404 完全播不了）--")
    captured.clear()
    bridge._play_episode(7065887)
    check("PGC 能播（不抛异常）", bool(captured), str(captured.get("title"))[:40])
    check("PGC 拿到直链", bool(captured.get("vurl")))
    check("PGC 历史写入", len(bridge.history) == 2 and bridge.history[1]["platform"] == "番剧",
          str([h["platform"] for h in bridge.history]))

    # ---- 画质上限生效 ----
    print("\n-- 画质上限 720P --")
    captured.clear()
    bridge.general_cfg = dict(bridge.general_cfg, quality="64")
    bridge._play_bili(bv)
    check("锁 720P 后历史记的是 720P 档",
          "720P" in bridge.history[-1]["detail"],
          bridge.history[-1]["detail"][:40])

    return finish()


def finish():
    n_pass = sum(1 for _, ok_, _ in results if ok_)
    print("\n" + "=" * 68)
    print(f"共 {len(results)} 项，通过 {n_pass} 项")
    if n_pass == len(results):
        print("ALL PASS")
        return 0
    print("FAILED:")
    for name, ok_, detail in results:
        if not ok_:
            print(f"  - {name}  {detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
