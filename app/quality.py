# -*- coding: utf-8 -*-
"""B 站画质档位 —— UI 设置项与 legacy 播放链路的唯一事实来源。

`general.conf` 里存的是档位字符串（"auto" / "127" / "80" …），qml 设置页直接列
出 `QUALITY_OPTIONS`；播放时用 `qn_from_pref()` 换算成 playurl 的 qn，
再用 `pick_stream()` 从 dash.video 里挑实际流。

挑流为什么以 id 为主键：B 站 dash 的 `video[].id` 就是画质档位本身
（127=8K / 126=杜比 / 125=HDR / 120=4K / 116=1080P60 / 112=1080P+ / 80=1080P /
64=720P / 32=480P / 16=360P），比 height/bandwidth 的语义更准 —— 同档位会有
avc/hevc/av1 多份编码，分辨率相同但码率/编码不同，先认 id 再认 bandwidth 才对。
"""

QUALITY_OPTIONS = (
    ("auto", "自动（最高可用）"),
    ("127", "8K"),
    ("126", "杜比视界"),
    ("125", "HDR"),
    ("120", "4K"),
    ("116", "1080P60"),
    ("112", "1080P+"),
    ("80", "1080P"),
    ("64", "720P"),
    ("32", "480P"),
    ("16", "360P"),
)

_LABELS = dict(QUALITY_OPTIONS)

# 无偏好时请求的最高档位：126 是 B 站 playurl 的常规最高请求值，
# 实际能拿到什么由账号权限（大会员）和视频本身决定。
_DEFAULT_QN = 126


def _int(stream, key: str) -> int:
    """取流里的整数指标（id / bandwidth / height …）。脏值（None、""、"abc"）当 0，不抛。"""
    try:
        return int(stream.get(key) or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def stream_bandwidth(stream) -> int:
    """流的码率，脏值当 0。音轨挑选（FLAC / 杜比）也用它。"""
    return _int(stream, "bandwidth")


def qn_from_pref(pref) -> int:
    """画质偏好 → playurl 的 qn。"auto" / 空 / 非法 / 非正数 → 126（最高请求档）。"""
    try:
        qn = int(str(pref).strip())
    except (TypeError, ValueError):
        return _DEFAULT_QN
    return qn if qn > 0 else _DEFAULT_QN


def format_pick(stream) -> str:
    """流的一行描述，写历史/日志用，如 "1080P60 1920x1080 avc1.640032"。"""
    if not isinstance(stream, dict):
        return ""
    qn = _int(stream, "id")
    label = _LABELS.get(str(qn)) if qn > 0 else ""
    parts = [label] if label else []
    width, height = _int(stream, "width"), _int(stream, "height")
    if width > 0 and height > 0:
        parts.append(f"{width}x{height}")
    elif height > 0 and not parts:
        parts.append(f"{height}p")
    codec = stream.get("codecs") or stream.get("codecid")
    if codec:
        parts.append(str(codec))
    if not parts:
        parts.append(f"id={qn}" if qn > 0 else "未知流")
    return " ".join(parts)


def pick_stream(vids, max_qn: int = 0):
    """从 dash.video 里挑最好的流，返回 (best | None, why)。

    规则，按优先级：
      1. `max_qn > 0` 时丢掉 id 超过上限的档位（用户画质偏好就是一个上限）；
      2. 按 `id` 降序 —— id 就是画质档位，语义最准；
      3. 同 id 再按 `bandwidth` 降序（同档位的 avc/hevc/av1 取码率最高的）；
      4. `id` 缺失或为 0 的脏流不能因此被排到最前 —— 它们不参与 id 排序，
         只有在没有任何带 id 的流时才启用，按 (height, bandwidth) 降序挑。
    """
    streams = [v for v in (vids or []) if isinstance(v, dict)]
    if max_qn > 0:
        streams = [v for v in streams if _int(v, "id") <= max_qn]

    ranked = [v for v in streams if _int(v, "id") > 0]
    if ranked:
        best = max(ranked, key=lambda s: (_int(s, "id"), _int(s, "bandwidth")))
        return best, (
            f"档位最高: id={_int(best, 'id')} "
            f"bandwidth={_int(best, 'bandwidth')}"
            + (f" (max_qn={max_qn})" if max_qn > 0 else "")
        )

    if streams:
        best = max(streams, key=lambda s: (_int(s, "height"), _int(s, "bandwidth")))
        return best, (
            f"流缺 id，按 height/bandwidth 挑: {_int(best, 'height')}p "
            f"bandwidth={_int(best, 'bandwidth')}"
        )

    return None, "无可用视频流"
