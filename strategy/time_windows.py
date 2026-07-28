"""strategy/time_windows.py — 时段推荐策略.

T3.3c: 根据天文数据/月相/季节推荐最佳作钓时段.

逻辑:
  - 黄金时段（日出后1h / 日落前1h）必推
  - 月相高分区（score > 0.7）加推月出/月落时段
  - 夏季加推夜钓窗口
  - 冬季主推午间窗口
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def recommend_time_windows(analysis_result: dict[str, Any]) -> dict[str, Any]:
    """推荐作钓时段.

    Args:
        analysis_result: analyze_fishing() 返回的分析结果

    Returns:
        {windows: [{type, start, end, priority, reason}], best_window, note}
    """
    sub_results = analysis_result.get("sub_results", {})
    conditions = analysis_result.get("conditions", {})
    astronomy = conditions.get("astronomy", {}) if conditions else {}

    solunar_result = sub_results.get("solunar", {})
    solunar_score = solunar_result.get("score", 0.5)

    season_info = sub_results.get("season", {})
    season = season_info.get("season", "spring") if isinstance(season_info, dict) else "spring"

    windows: list[dict[str, str]] = []

    # --- 1. 黄金时段（必推） ---
    golden_morning = astronomy.get("golden_hour_morning")
    golden_evening = astronomy.get("golden_hour_evening")

    if golden_morning:
        # golden_hour_morning = [start_iso, end_iso] (serialized tuple)
        gm = golden_morning if isinstance(golden_morning, list) else [golden_morning]
        if len(gm) >= 2:
            windows.append({
                "type": "黄金晨窗",
                "start": _extract_time(gm[0]),
                "end": _extract_time(gm[1]),
                "priority": "high",
                "reason": "日出后1小时, 鱼活性最高",
            })
    else:
        windows.append({
            "type": "默认晨窗",
            "start": "05:00",
            "end": "08:00",
            "priority": "high",
            "reason": "无天文数据, 默认晨间最佳",
        })

    if golden_evening:
        ge = golden_evening if isinstance(golden_evening, list) else [golden_evening]
        if len(ge) >= 2:
            windows.append({
                "type": "黄金昏窗",
                "start": _extract_time(ge[0]),
                "end": _extract_time(ge[1]),
                "priority": "high",
                "reason": "日落前1小时, 鱼活性最高",
            })
    else:
        windows.append({
            "type": "默认昏窗",
            "start": "17:00",
            "end": "20:00",
            "priority": "high",
            "reason": "无天文数据, 默认黄昏最佳",
        })

    # --- 2. 月相时段（高分区加推） ---
    if solunar_score > 0.7:
        moonrise = astronomy.get("moonrise")
        moonset = astronomy.get("moonset")
        if moonrise:
            windows.append({
                "type": "月出窗",
                "start": _extract_time(moonrise),
                "end": _shift_time(moonrise, 90),
                "priority": "medium",
                "reason": f"月相评分 {solunar_score:.2f}, 月出前后鱼活跃",
            })
        if moonset:
            windows.append({
                "type": "月落窗",
                "start": _shift_time(moonset, -90),
                "end": _extract_time(moonset),
                "priority": "medium",
                "reason": f"月相评分 {solunar_score:.2f}, 月落前后鱼活跃",
            })

    # --- 3. 季节窗口 ---
    if season == "summer":
        windows.append({
            "type": "夜钓窗",
            "start": "20:00",
            "end": "23:00",
            "priority": "medium",
            "reason": "夏季夜间降温, 大鱼出没",
        })
    elif season == "winter":
        windows.append({
            "type": "午间窗",
            "start": "11:00",
            "end": "14:00",
            "priority": "high",
            "reason": "冬季午间水温最高, 鱼觅食",
        })

    # --- 最佳窗口 ---
    best = windows[0] if windows else None

    logger.info(
        "time_windows: %d windows, best=%s",
        len(windows), best["type"] if best else "none",
    )

    return {
        "windows": windows,
        "best_window": best,
        "solunar_score": round(solunar_score, 3),
    }


def _extract_time(dt_str: Any) -> str:
    """从 ISO datetime 字符串提取 HH:MM."""
    s = str(dt_str)
    if "T" in s:
        s = s.split("T")[1]
    if "+" in s:
        s = s.split("+")[0]
    return s[:5] if len(s) >= 5 else s


def _shift_time(dt_str: Any, minutes: int) -> str:
    """时间偏移 minutes 分钟, 返回 HH:MM."""
    s = _extract_time(dt_str)
    try:
        h, m = int(s[:2]), int(s[3:5])
        total = h * 60 + m + minutes
        total = max(0, min(1439, total))
        return f"{total // 60:02d}:{total % 60:02d}"
    except (ValueError, IndexError):
        return s
