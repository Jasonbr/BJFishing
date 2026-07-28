"""engine/solunar.py — 月相评分.

T1.5: 月相对鱼活性的影响.
原理: 月相影响鱼类摄食节律（月相上下弦前后活跃）.
数据源: services/astronomy.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from services.astronomy import get_moon_phase

logger = logging.getLogger(__name__)

# 月相周期（天）
_LUNAR_CYCLE = 29.53


@dataclass(frozen=True)
class SolunarResult:
    """月相评分结果."""

    moon_phase: float  # 月相值（0-29.53）
    moon_phase_name: str  # 月相名称
    moon_illumination: float  # 月亮照度（0-1）
    score: float  # 0-1
    note: str


def score_solunar(date: date | datetime | None = None) -> SolunarResult:
    """月相评分.

    Args:
        date: 日期，None=今天

    Returns:
        SolunarResult: 月相评分

    原理:
        - 新月/满月前后：鱼活跃度最高（score=1.0）
        - 上弦/下弦：中等活跃（score=0.6）
        - 其他相位：一般（score=0.4-0.5）
    """
    phase, name, illumination = get_moon_phase(date)

    # 计算距新月/满月的距离（0-14.77 天）
    # 新月=0, 满月=14.77
    dist_to_new = min(phase, _LUNAR_CYCLE - phase)  # 距新月
    dist_to_full = abs(phase - _LUNAR_CYCLE / 2)     # 距满月
    min_dist = min(dist_to_new, dist_to_full)        # 距最近的关键相位

    # 计算距上弦/下弦的距离
    dist_to_first_quarter = abs(phase - _LUNAR_CYCLE / 4)   # 距上弦（~7.38）
    dist_to_last_quarter = abs(phase - 3 * _LUNAR_CYCLE / 4)  # 距下弦（~22.15）
    min_quarter_dist = min(dist_to_first_quarter, dist_to_last_quarter)

    if min_dist < 1.0:
        # 新月/满月前后 1 天：最高分
        score = 1.0
        note = f"{name}前后，鱼活性最高"
    elif min_dist < 2.0:
        # 关键相位前后 2 天
        score = 0.85
        note = f"{name}，接近关键相位"
    elif min_quarter_dist < 1.5:
        # 上弦/下弦前后 1.5 天
        score = 0.7
        note = f"{name}，中等活跃"
    elif min_dist < 3.5:
        # 接近关键相位
        score = 0.6
        note = f"{name}，接近弦月"
    else:
        # 其他相位
        score = 0.5
        note = f"{name}，一般活跃"

    logger.info("solunar: phase=%.2f(%s) illum=%.1f%% score=%.2f",
                phase, name, illumination * 100, score)
    return SolunarResult(
        moon_phase=phase,
        moon_phase_name=name,
        moon_illumination=illumination,
        score=score,
        note=note,
    )
