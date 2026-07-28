"""engine/wind.py — 风况评分.

T1.6: 风速风向对钓鱼的影响.
原理:
  - 微风(1-3m/s)：最佳，增氧+搅动食物
  - 无风(0-1m/s)：一般，溶氧不足
  - 大风(>8m/s)：差，难操作+水浑
  - 风向：迎风岸食物多，鱼聚集
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 风速评分阈值（m/s）
_WIND_CALM = 1.0      # 无风
_WID_BREEZE = 3.0     # 微风
_WIND_MODERATE = 6.0  # 中风
_WIND_STRONG = 8.0    # 大风
_WIND_GALE = 10.0     # 狂风


@dataclass(frozen=True)
class WindResult:
    """风况评分结果."""

    wind_speed_ms: float  # 风速（m/s）
    wind_direction_deg: float | None  # 风向（度），None=未知
    score: float  # 0-1
    level: str  # optimal/breeze/moderate/strong/dangerous
    note: str


def score_wind(
    wind_speed_ms: float,
    wind_direction_deg: float | None = None,
    season: str | None = None,
) -> WindResult:
    """风况评分.

    Args:
        wind_speed_ms: 风速（m/s）
        wind_direction_deg: 风向（度 0-360），None=未知
        season: 季节（spring/summer/autumn/winter），影响风向调整

    Returns:
        WindResult: 评分 + 等级
    """
    if wind_speed_ms < _WIND_CALM:
        score = 0.5
        level = "calm"
        note = f"无风({wind_speed_ms:.1f}m/s)，溶氧不足"
    elif wind_speed_ms <= _WID_BREEZE:
        score = 1.0
        level = "optimal"
        note = f"微风({wind_speed_ms:.1f}m/s)，增氧+食物丰富"
    elif wind_speed_ms <= _WIND_MODERATE:
        score = 0.7
        level = "moderate"
        note = f"中风({wind_speed_ms:.1f}m/s)，可钓但需注意"
    elif wind_speed_ms <= _WIND_STRONG:
        score = 0.4
        level = "strong"
        note = f"大风({wind_speed_ms:.1f}m/s)，操作困难"
    elif wind_speed_ms <= _WIND_GALE:
        score = 0.2
        level = "dangerous"
        note = f"狂风({wind_speed_ms:.1f}m/s)，不建议出钓"
    else:
        score = 0.1
        level = "dangerous"
        note = f"暴风({wind_speed_ms:.1f}m/s)，危险"

    # 风向-季节交互评分
    dir_adj = 0.0
    if wind_direction_deg is not None and season is not None:
        is_south = 135 <= wind_direction_deg <= 225
        is_north = wind_direction_deg >= 315 or wind_direction_deg <= 45
        if season == "summer":
            if is_south:
                dir_adj = 0.1
            elif is_north:
                dir_adj = -0.1
        elif season == "winter":
            if is_south:
                dir_adj = 0.15
            elif is_north:
                dir_adj = -0.15
        score = max(0.0, min(1.0, score + dir_adj))
        if dir_adj != 0.0:
            note += f"（风向季节调整{dir_adj:+.1f}）"

    logger.info("wind: %.1f m/s level=%s score=%.2f", wind_speed_ms, level, score)
    return WindResult(
        wind_speed_ms=wind_speed_ms,
        wind_direction_deg=wind_direction_deg,
        score=score,
        level=level,
        note=note,
    )
