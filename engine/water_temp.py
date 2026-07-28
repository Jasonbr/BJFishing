"""engine/water_temp.py — 水温估算.

T1.1: 基于 3 日均气温估算水温.
公式: temp_air_3d_avg * 0.75 + 3.5（基础）
水域调整: 水库 -2 / 河流 0 / 黑坑 +1

被 tools/analyze.py / engine/scoring.py 引用.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from services.weather import get_historical_avg_temp

logger = logging.getLogger(__name__)

# 水温估算系数
_BASE_COEFF = 0.75  # 气温→水温转换系数
_BASE_OFFSET = 3.5  # 基础偏移（℃）

# 水域类型调整（℃）
_WATER_TYPE_ADJUST: dict[str, float] = {
    "reservoir": -2.0,  # 水库深水，水温偏低
    "river": 0.0,      # 河流水流，居中
    "black_pit": 1.0,  # 黑坑浅水/人工，水温偏高
}


@dataclass(frozen=True)
class WaterTempResult:
    """水温估算结果."""

    water_temp_c: float  # 估算水温（℃）
    air_temp_3d_avg: float | None  # 3 日均气温（℃），None=获取失败
    water_type: str  # 水域类型
    adjustment: float  # 水域调整值
    data_quality: str  # full/partial/degraded
    note: str  # 说明


def estimate_water_temp(
    lat: float,
    lng: float,
    water_type: str = "river",
    days: int = 3,
) -> WaterTempResult:
    """估算水温.

    Args:
        lat: 纬度
        lng: 经度
        water_type: 水域类型 (reservoir/river/black_pit)
        days: 回溯天数（默认 3）

    Returns:
        WaterTempResult: 估算结果

    降级策略:
        - historical API 正常 → data_quality=full
        - historical API 失败 → 用当前气温代替，data_quality=partial
        - 当前气温也失败 → 用季节默认值，data_quality=degraded
    """
    wt = water_type.lower().strip()
    if wt not in _WATER_TYPE_ADJUST:
        logger.warning("Unknown water_type %r, defaulting to river", water_type)
        wt = "river"
    adjustment = _WATER_TYPE_ADJUST[wt]

    # 1. 尝试获取 3 日均气温（historical API）
    air_avg = get_historical_avg_temp(lat, lng, days=days)

    if air_avg is not None:
        # 正常路径
        water_temp = air_avg * _BASE_COEFF + _BASE_OFFSET + adjustment
        logger.info(
            "water_temp: air_avg=%.1f base=%.1f adj=%.1f → water=%.1f",
            air_avg, _BASE_OFFSET, adjustment, water_temp,
        )
        return WaterTempResult(
            water_temp_c=round(water_temp, 1),
            air_temp_3d_avg=round(air_avg, 1),
            water_type=wt,
            adjustment=adjustment,
            data_quality="full",
            note=f"historical {days}d avg={air_avg:.1f}C",
        )

    # 2. 降级：用当前气温代替
    from services.weather import get_weather
    weather = get_weather(lat, lng)
    if weather and "current" in weather:
        current_temp = weather["current"].get("temperature_2m")
        if current_temp is not None:
            water_temp = float(current_temp) * _BASE_COEFF + _BASE_OFFSET + adjustment
            logger.warning("historical failed, using current temp %.1f", current_temp)
            return WaterTempResult(
                water_temp_c=round(water_temp, 1),
                air_temp_3d_avg=None,
                water_type=wt,
                adjustment=adjustment,
                data_quality="partial",
                note=f"historical unavailable, current={current_temp}C",
            )

    # 3. 最终降级：季节默认值
    from engine.season import get_season
    season_info = get_season()
    # 季节性默认水温（北京）
    season_defaults = {
        "spring": 15.0,
        "summer": 26.0,
        "autumn": 14.0,
        "winter": 3.0,
    }
    default_temp = season_defaults.get(season_info.season, 15.0) + adjustment
    logger.error("weather data unavailable, using season default %.1f", default_temp)
    return WaterTempResult(
        water_temp_c=round(default_temp, 1),
        air_temp_3d_avg=None,
        water_type=wt,
        adjustment=adjustment,
        data_quality="degraded",
        note=f"weather unavailable, season default={default_temp:.1f}C",
    )
