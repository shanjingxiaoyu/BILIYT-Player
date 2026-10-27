# -*- coding: utf-8 -*-
"""画质链路自检 —— 真实联网（读 %APPDATA%\\BiliYTPlayer\\.env 的 SESSDATA）。

覆盖 5 件事：
  1. UGC 回归：普通视频挑中档位不能被修复弄差（== accept_quality[0]）
  2. PGC 修复：ep7065887 走 pgc 回落能拿到 dash 并挑出流
  3. max_qn 生效：偏好 720P 时挑中的 id <= 64
  4. 脏数据不崩：dolby/flac/audio 各种畸形形状喂给 pick_dolby_streams
  5. 向后兼容：get_playurl 不传 qn 仍能返回

用法: python _verify_quality.py     （非 0 退出码 = 有 FAIL）
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "legacy"))

import bili_clipboard_dolby as B          # noqa: E402
from app.quality import (                 # noqa: E402
    QUALITY_OPTIONS, format_pick, pick_stream, qn_from_pref,
)

# UGC 回归样本：三个不同体量的普通投稿（排行榜前几名，覆盖面最广）
UGC_BVS = ["BV1GJ411x7h7"]
PGC_EP = 7065887

_fails = []


def check(name, ok, detail):
    tag = "PASS" if ok else "FAIL"
    if not ok:
        _fails.append(name)
    print(f"[{tag}] {name}: {detail}")


def main():
    print("=" * 68)
    print(" BILIYTPlayer 画质链路自检（真实联网）")
    print("=" * 68)

    # ── case 0: 纯离线 —— 档位映射 / 挑流规则 ──────────────────────────
    print("\n--- case 0: 档位映射与挑流规则（离线） ---")
    check("qn_from_pref(auto)", qn_from_pref("auto") == 126, f"{qn_from_pref('auto')}")
    check("qn_from_pref(80)", qn_from_pref("80") == 80, f"{qn_from_pref('80')}")
    check("qn_from_pref(脏值)", qn_from_pref("abc") == 126, f"{qn_from_pref('abc')}")
    check("QUALITY_OPTIONS 首项", QUALITY_OPTIONS[0] == ("auto", "自动（最高可用）"),
          f"{QUALITY_OPTIONS[0]}")

    dirty = [
        {"id": 80, "bandwidth": 2000, "width": 1920, "height": 1080, "codecs": "avc1.640028"},
        {"id": 116, "bandwidth": 5000, "width": 1920, "height": 1080, "codecs": "avc1.640032"},
        {"id": 0, "bandwidth": 99999, "width": 7680, "height": 4320},  # 脏流：不该排最前
        {"bandwidth": 88888, "width": 3840, "height": 2160},           # 缺 id：不该排最前
    ]
    best, why = pick_stream(dirty)
    check("id 缺失/为 0 不抢首位", (best or {}).get("id") == 116, f"pick={best.get('id')} ({why})")
    clean = [{"id": q, "bandwidth": q * 10, "height": q} for q in (116, 80, 64, 32, 16)]
    best64, _ = pick_stream(clean, 64)
    check("max_qn=64 离线生效", (best64 or {}).get("id") == 64, f"pick={best64}")
    check("max_qn 过滤后仍有脏流可用", (pick_stream(dirty, 64)[0] or {}).get("id", 0) <= 64,
          f"pick={pick_stream(dirty, 64)[0]}")
    check("空列表返回 (None, 无可用视频流)", pick_stream([]) == (None, "无可用视频流"),
          f"{pick_stream([])}")
    check("format_pick", format_pick({"id": 116, "width": 1920, "height": 1080,
                                     "codecs": "avc1.640032"}) == "1080P60 1920x1080 avc1.640032",
          format_pick({"id": 116, "width": 1920, "height": 1080, "codecs": "avc1.640032"}))

    # ── case 4: 脏数据不崩（离线，放在联网前，网络挂了也能跑） ────────
    print("\n--- case 4: pick_dolby_streams 脏数据不崩（离线） ---")
    variants = [
        ("dolby dict+空 video",
         {"video": [], "audio": {"base_url": "x"},
          "dolby": {"audio": {"base_url": "y"}, "video": {"base_url": "z"}}}),
        ("dolby 全空", {"video": [], "audio": [], "dolby": {}}),
        ("flac 单 dict / 驼峰字段",
         {"video": [{"id": 80, "baseUrl": "v", "backupUrl": ["b"]}], "audio": None,
          "flac": {"audio": {"base_url": "f"}}}),
        ("非法类型", {"video": None, "audio": "garbage", "dolby": "garbage"}),
        ("全空 dash", {}),
        ("dash 本身 None", None),
        ("dolby.audio=None 但有普通轨",
         {"video": [{"id": 80, "base_url": "v"}], "audio": [{"id": 30280, "base_url": "a"}],
          "dolby": {"audio": None, "video": None}, "flac": None}),
        ("只有 backup_url", {"video": [{"id": 80, "backup_url": ["bak"]}], "audio": []}),
        ("base_url 为空串但有 backupUrl",
         {"video": [{"id": 80, "base_url": "", "backupUrl": ["bak2"]}], "audio": []}),
    ]
    for label, payload in variants:
        try:
            got = B.pick_dolby_streams(payload)
            check(f"不崩: {label}", isinstance(got, tuple) and len(got) == 4, f"{got}")
        except Exception as e:  # noqa: BLE001
            check(f"不崩: {label}", False, f"RAISED {type(e).__name__}: {e}")

    # ── 联网部分 ──────────────────────────────────────────────────────
    print("\n--- 建立 B 站会话 ---")
    try:
        sessdata = B.load_sessdata()
        session, img_key, sub_key = B.init_bili_session(sessdata)
        print("[PASS] SESSDATA 有效，WBI 密钥就绪")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 会话建立失败: {type(e).__name__}: {e}")
        print("\nFAIL 1（无法联网，联网用例全部跳过）")
        return 1

    # ── case 5: 向后兼容（不传 qn） ───────────────────────────────────
    print("\n--- case 5: get_playurl 向后兼容（不传 qn） ---")
    bv = UGC_BVS[0]
    cid, title = B.get_cid(session, bv)
    try:
        legacy = B.get_playurl(session, bv, cid, img_key, sub_key)  # 旧签名
        ok = isinstance(legacy, dict) and bool(legacy.get("dash"))
        check("get_playurl(不传 qn)", ok,
              f"{len((legacy.get('dash') or {}).get('video') or [])} 条视频流, "
              f"accept={(legacy.get('accept_quality') or [])[:4]}")
    except Exception as e:  # noqa: BLE001
        check("get_playurl(不传 qn)", False, f"RAISED {type(e).__name__}: {e}")

    # ── case 1: UGC 回归 ─────────────────────────────────────────────
    print("\n--- case 1: UGC 回归（挑中档位 == accept_quality[0]） ---")
    try:
        r = session.get(
            "https://api.bilibili.com/x/web-interface/ranking/v2",
            params={"rid": 0, "type": "all"}, headers=B.COMMON_HEADERS, timeout=10,
        )
        ranked = [x["bvid"] for x in (r.json().get("data") or {}).get("list") or []]
    except Exception as e:  # noqa: BLE001
        ranked = []
        print(f"[!] 排行榜拉取失败，退回固定样本: {e}")
    samples = [b for b in ranked[:3]] or UGC_BVS

    for bv in samples:
        try:
            cid, title = B.get_cid(session, bv)
            data = B.get_playurl(session, bv, cid, img_key, sub_key)
            top = (data.get("accept_quality") or [None])[0]
            best, why = pick_stream((data.get("dash") or {}).get("video") or [], 0)
            pid = (best or {}).get("id")
            check(f"UGC {bv}", pid == top and pid is not None,
                  f"accept={top} pick={pid} desc={format_pick(best)} {title[:16]}")
        except Exception as e:  # noqa: BLE001
            check(f"UGC {bv}", False, f"RAISED {type(e).__name__}: {e}")

    # ── case 3: max_qn 生效 ─────────────────────────────────────────
    print("\n--- case 3: max_qn=64（720P 上限） ---")
    bv = samples[0]
    try:
        cid, title = B.get_cid(session, bv)
        data = B.get_playurl(session, bv, cid, img_key, sub_key)
        vids = (data.get("dash") or {}).get("video") or []
        unlimited, _ = pick_stream(vids, 0)
        capped, why = pick_stream(vids, 64)
        ok = capped is not None and (capped.get("id") or 0) <= 64
        check("max_qn=64", ok,
              f"不限={ (unlimited or {}).get('id')} → 上限64={ (capped or {}).get('id')} "
              f"({format_pick(capped)})")
        vurl, aurl, vd, ad = B.pick_dolby_streams(data.get("dash") or {}, 64)
        check("pick_dolby_streams(max_qn=64)", vurl is not None,
              f"vdesc={vd!r} adesc={ad!r}")
    except Exception as e:  # noqa: BLE001
        check("max_qn=64", False, f"RAISED {type(e).__name__}: {e}")

    # ── case 2: PGC 修复 ─────────────────────────────────────────────
    print(f"\n--- case 2: PGC 修复（ep{PGC_EP}） ---")
    try:
        bvid, cid, full_title = B.resolve_episode(session, PGC_EP)
        print(f"       ep{PGC_EP} → {bvid} cid={cid} {full_title[:28]}")
        data = B.get_playurl(session, bvid, cid, img_key, sub_key)
        vids = (data.get("dash") or {}).get("video") or []
        best, _why = pick_stream(vids, 0)
        vurl, aurl, vd, ad = B.pick_dolby_streams(data.get("dash") or {})
        check("PGC get_playurl 不抛", True,
              f"accept={(data.get('accept_quality') or [])[:4]} {len(vids)} 条流")
        check("PGC 挑出可播视频流", vurl is not None and (best or {}).get("id") is not None,
              f"挑中 {format_pick(best)} / vdesc={vd!r}")
        check("PGC 音轨", aurl is not None, f"adesc={ad!r}")
        print(f"       playurl 顶层键: {sorted(data.keys())[:8]}")
    except Exception as e:  # noqa: BLE001
        check("PGC 修复", False, f"RAISED {type(e).__name__}: {e}")

    # ── 汇总 ────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    if _fails:
        print(f"FAIL {len(_fails)}: {', '.join(_fails)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
