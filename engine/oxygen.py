"""engine/oxygen.py — 溶氧推算.

T1.2: 基于水温估算溶解氧.
公式: sat_o2 = 14.6 - 0.4*T + 0.008*T²
（T = 水温 ℃，标准大气压下的饱和溶氧量）

被 tools/analyze.py / engine/scoring.py 引用.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 溶氧饱和公式系数（标准大气压）
_O2_CONST = 14.6   # 0℃ 时饱和溶氧
_O2_TEMP_COEFF = 0.4   # 一次项
_O2_TEMP_SQUARED = 0.008   # 二次项

# 溶氧评分阈值（mg/L）
_O2_EXCELLENT = 8.0   # 溶氧充足
_O2_GOOD = 6.0        # 溶氧适中
_O2_POOR = 4.0        # 溶氧偏低
_O2_CRITICAL = 2.0    # 溶氧危险


@dataclass(frozen=True)
class OxygenResult:
    """溶氧估算结果."""

    dissolved_o2_mg_l: float  # 估算溶氧（mg/L）
    water_temp_c: float  # 输入水温（℃）
    saturation_percent: float  # 饱和度（%）
    score: float  # 评分 0-1
    level: str  # excellent/good/poor/critical
    note: str


def estimate_oxygen(water_temp_c: float) -> OxygenResult:
    """估算溶解氧.

    Args:
        water_temp_c: 水温（℃）

    Returns:
        OxygenResult: 溶氧估算 + 评分

    原理:
        - 低温水溶氧高，高温水溶氧低
        - 饱和溶氧量: 14.6 - 0.4*T + 0.008*T²
        - 假设 100% 饱和（实际受风/流/水生植物影响）
    """
    t = water_temp_c
    sat_o2 = _O2_CONST - _O2_TEMP_COEFF * t + _O2_TEMP_SQUARED * (t ** 2)

    # 评分：溶氧越高越好
    if sat_o2 >= _O2_EXCELLENT:
        score = 1.0
        level = "excellent"
        note = f"溶氧充足({sat_o2:.1f}mg/L)"
    elif sat_o2 >= _O2_GOOD:
        score = 0.7
        level = "good"
        note = f"溶氧适中({sat_o2:.1f}mg/L)"
    elif sat_o2 >= _O2_POOR:
        score = 0.4
        level = "poor"
        note = f"溶氧偏低({sat_o2:.1f}mg/L)，鱼活性下降"
    elif sat_o2 >= _O2_CRITICAL:
        score = 0.2
        level = "critical"
        note = f"溶氧危险({sat_o2:.1f}mg/L)，鱼可能浮头"
    else:
        score = 0.1
        level = "critical"
        note = f"溶氧极低({sat_o2:.1f}mg/L)，严重缺氧"

    logger.info("oxygen: T=%.1f sat_o2=%.2f level=%s score=%.2f", t, sat_o2, level, score)
    return OxygenResult(
        dissolved_o2_mg_l=round(sat_o2, 2),
        water_temp_c=t,
        saturation_percent=100.0,  # 假设饱和
        score=score,
        level=level,
        note=note,
    )
