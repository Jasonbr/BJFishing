"""engine/precipitation.py — 降水评分.

T1.7: 降水对钓鱼的影响.
原理:
  - 无雨/小雨：好（增氧+食物冲入）
  - 中雨：一般（可钓但鱼分散）
  - 大雨/暴雨：差（危险+水浑+鱼惊）
  - 雪天：冰钓季专属
  - 前一天大雨当天转晴：最佳窗口
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 降水评分阈值（mm/day）
_PRECIP_NONE = 0.1      # 无雨
_PRECIP_LIGHT = 2.5     # 小雨
_PRECIP_MODERATE = 8.0  # 中雨
_PRECIP_HEAVY = 16.0    # 大雨
_PRECIP_STORM = 25.0    # 暴雨


@dataclass(frozen=True)
class PrecipitationResult:
    """降水评分结果."""

    precip_mm: float  # 当日降水（mm）
    prev_precip_mm: float | None  # 前日降水（mm），None=未知
    score: float  # 0-1
    level: str  # none/light/moderate/heavy/storm
    note: str


def score_precipitation(
    precip_mm: float,
    prev_precip_mm: float | None = None,
) -> PrecipitationResult:
    """降水评分.

    Args:
        precip_mm: 当日降水量（mm）
        prev_precip_mm: 前日降水量（mm），用于判断雨后窗口

    Returns:
        PrecipitationResult: 评分 + 等级
    """
    if precip_mm < _PRECIP_NONE:
        if prev_precip_mm is not None and prev_precip_mm >= _PRECIP_MODERATE:
            # 前日大雨+今天转晴：最佳窗口
            score = 1.0
            level = "post_rain"
            note = f"雨后转晴(前日{prev_precip_mm:.1f}mm)，最佳钓鱼窗口"
        else:
            score = 0.7
            level = "none"
            note = "无降水"
    elif precip_mm <= _PRECIP_LIGHT:
        score = 0.9
        level = "light"
        note = f"小雨({precip_mm:.1f}mm)，增氧+食物丰富"
    elif precip_mm <= _PRECIP_MODERATE:
        score = 0.6
        level = "moderate"
        note = f"中雨({precip_mm:.1f}mm)，可钓但鱼分散"
    elif precip_mm <= _PRECIP_HEAVY:
        score = 0.3
        level = "heavy"
        note = f"大雨({precip_mm:.1f}mm)，鱼惊+水浑"
    elif precip_mm <= _PRECIP_STORM:
        score = 0.1
        level = "storm"
        note = f"暴雨({precip_mm:.1f}mm)，不建议出钓"
    else:
        score = 0.05
        level = "storm"
        note = f"大暴雨({precip_mm:.1f}mm)，危险"

    logger.info("precip: %.1f mm level=%s score=%.2f", precip_mm, level, score)
    return PrecipitationResult(
        precip_mm=precip_mm,
        prev_precip_mm=prev_precip_mm,
        score=score,
        level=level,
        note=note,
    )
