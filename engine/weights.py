"""engine/weights.py — 动态评分权重.

T1.8: 8 维评分公式的权重管理.
公式: fishing_score = Σ(score_i * weight_i) + feedback_adjustment

权重默认值:
  pressure: 0.25
  temperature: 0.20
  solunar: 0.15
  wind: 0.10
  precipitation: 0.10
  season: 0.10
  water (temp+oxygen): 0.05
  feedback: ±0.10 (调整项)
  Total = 0.95 + 0.05 = 1.00

动态调整: 不同季节/水域类型权重略调整
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from engine.season import get_season

logger = logging.getLogger(__name__)

# 默认权重（总和 = 0.95，feedback 占 0.05）
DEFAULT_WEIGHTS: dict[str, float] = {
    "pressure": 0.25,
    "temperature": 0.20,
    "solunar": 0.15,
    "wind": 0.10,
    "precipitation": 0.10,
    "season": 0.10,
    "water": 0.05,
}

# feedback 调整范围
FEEDBACK_ADJUSTMENT_MAX = 0.10  # ±10%

# 季节性权重调整
_SEASON_ADJUSTMENTS: dict[str, dict[str, float]] = {
    "spring": {
        # 春季产卵期，温度+月相更重要
        "temperature": +0.05,
        "solunar": +0.03,
        "pressure": -0.04,
        "precipitation": -0.04,
    },
    "summer": {
        # 夏季高温，气压+溶氧更重要
        "pressure": +0.05,
        "water": +0.03,
        "solunar": -0.03,
        "season": -0.05,
    },
    "autumn": {
        # 秋季贴秋膘，温度最重要
        "temperature": +0.05,
        "precipitation": +0.02,
        "wind": -0.02,
        "solunar": -0.05,
    },
    "winter": {
        # 冬季冰钓，温度主导
        "temperature": +0.10,
        "water": +0.03,
        "wind": -0.03,
        "precipitation": -0.05,
        "solunar": -0.05,
    },
}


@dataclass(frozen=True)
class Weights:
    """评分权重."""

    pressure: float
    temperature: float
    solunar: float
    wind: float
    precipitation: float
    season: float
    water: float
    feedback_max: float
    total: float  # 实际权重总和（不含 feedback）
    season_name: str  # 当前季节
    adjustments: dict[str, float]  # 相对默认的调整量


def get_weights(season: str | None = None) -> Weights:
    """获取当前评分权重（动态调整）.

    Args:
        season: 指定季节，None=自动判断

    Returns:
        Weights: 7 个维度权重 + feedback 上限
    """
    if season is None:
        season = get_season().season

    weights = dict(DEFAULT_WEIGHTS)
    adjustments: dict[str, float] = {}
    season_adj = _SEASON_ADJUSTMENTS.get(season, {})
    for key, adj in season_adj.items():
        weights[key] = max(0.0, weights.get(key, 0.0) + adj)
        adjustments[key] = adj

    total = sum(weights.values())

    logger.info("weights: season=%s total=%.2f adj=%s", season, total, adjustments)
    return Weights(
        pressure=weights["pressure"],
        temperature=weights["temperature"],
        solunar=weights["solunar"],
        wind=weights["wind"],
        precipitation=weights["precipitation"],
        season=weights["season"],
        water=weights["water"],
        feedback_max=FEEDBACK_ADJUSTMENT_MAX,
        total=total,
        season_name=season,
        adjustments=adjustments,
    )


def compute_fishing_score(
    pressure_score: float,
    temperature_score: float,
    solunar_score: float,
    wind_score: float,
    precipitation_score: float,
    season_score: float,
    water_score: float,
    feedback_adjustment: float = 0.0,
    weights: Weights | None = None,
) -> float:
    """计算综合钓鱼评分.

    Args:
        7 个维度的分数（0-1）
        feedback_adjustment: 反馈调整（-0.10 ~ +0.10）
        weights: 自定义权重，None=自动获取

    Returns:
        综合评分（0-1）
    """
    if weights is None:
        weights = get_weights()

    # 限制 feedback 范围
    fb = max(-FEEDBACK_ADJUSTMENT_MAX, min(FEEDBACK_ADJUSTMENT_MAX, feedback_adjustment))

    score = (
        pressure_score * weights.pressure
        + temperature_score * weights.temperature
        + solunar_score * weights.solunar
        + wind_score * weights.wind
        + precipitation_score * weights.precipitation
        + season_score * weights.season
        + water_score * weights.water
    )

    # 归一化（权重总和可能 ≠ 1.0），feedback 不参与归一化
    if weights.total > 0:
        score = score / weights.total * 0.95 + fb

    # 限制到 0-1
    score = max(0.0, min(1.0, score))

    logger.info("fishing_score=%.3f (fb=%+.2f)", score, fb)
    return round(score, 3)
