"""engine/pressure.py — 气压评分.

T1.3: 气压变化趋势评分.
原理: 鱼对气压变化敏感
  - 气压稳定/微升 → 高分
  - 气压骤降 → 低分（鱼闭口）
  - 气压极低（<1000hPa）→ 减分
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 气压评分阈值（hPa）
_PRESSURE_NORMAL_MIN = 1000.0
_PRESSURE_NORMAL_MAX = 1025.0
_PRESSURE_LOW = 995.0
_PRESSURE_HIGH = 1030.0


@dataclass(frozen=True)
class PressureResult:
    """气压评分结果."""

    current_hpa: float  # 当前气压
    trend: str  # stable/rising/falling/unknown
    score: float  # 0-1
    note: str


def score_pressure(
    current_hpa: float,
    prev_hpa: float | None = None,
) -> PressureResult:
    """气压评分.

    Args:
        current_hpa: 当前气压（hPa）
        prev_hpa: 前序气压（用于趋势判断），None=未知

    Returns:
        PressureResult: 评分 + 趋势
    """
    # 趋势判断
    if prev_hpa is not None:
        delta = current_hpa - prev_hpa
        if abs(delta) < 1.0:
            trend = "stable"
        elif delta > 0:
            trend = "rising"
        else:
            trend = "falling"
    else:
        trend = "unknown"
        delta = 0.0

    # 基础分（先判正常范围 1000-1025，再判边缘范围 995-1030）
    if _PRESSURE_NORMAL_MIN <= current_hpa <= _PRESSURE_NORMAL_MAX:
        base_score = 1.0
    elif _PRESSURE_LOW <= current_hpa <= _PRESSURE_HIGH:
        base_score = 0.7
    else:
        base_score = 0.3  # 气压异常

    # 趋势调整
    if trend == "stable":
        trend_adj = 0.0  # 稳定不加分
    elif trend == "rising":
        trend_adj = 0.2  # 微升加分
    elif trend == "falling":
        if delta < -3.0:
            trend_adj = -0.5  # 骤降严重减分
        elif delta < -1.0:
            trend_adj = -0.3  # 下降减分
        else:
            trend_adj = -0.1  # 微降略减
    else:
        trend_adj = -0.1  # 未知趋势略减

    score = max(0.0, min(1.0, base_score + trend_adj))

    if trend == "falling" and delta < -3.0:
        note = f"气压骤降({delta:+.1f}hPa)，鱼可能闭口"
    elif trend == "falling":
        note = f"气压下降({delta:+.1f}hPa)"
    elif trend == "rising":
        note = f"气压上升({delta:+.1f}hPa)，鱼活性增加"
    elif trend == "stable":
        note = "气压稳定"
    else:
        note = f"气压{current_hpa:.1f}hPa（趋势未知）"

    logger.info("pressure: %.1f hPa trend=%s score=%.2f", current_hpa, trend, score)
    return PressureResult(
        current_hpa=current_hpa,
        trend=trend,
        score=score,
        note=note,
    )
