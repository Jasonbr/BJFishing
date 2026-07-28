"""strategy/position.py — 钓位推荐策略.

T3.3a: 根据风向/风速/水域类型/季节推荐钓位.

逻辑:
  - 河流 + 大风: 下游避风岸（鱼避风觅食）
  - 水库 + 大风: 迎风岸（浮游生物被吹来，鱼跟随）
  - 黑坑: 深水边缘/结构区
  - 无风: 靠近结构/入水口/回水湾
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def recommend_position(analysis_result: dict[str, Any]) -> dict[str, Any]:
    """推荐钓位.

    Args:
        analysis_result: analyze_fishing() 返回的分析结果

    Returns:
        {recommendation, reason, alternatives, wind_strategy}
    """
    sub_results = analysis_result.get("sub_results", {})
    conditions = analysis_result.get("conditions", {})
    weather = conditions.get("weather", {}) if conditions else {}
    current = weather.get("current", {}) if weather else {}

    wind_result = sub_results.get("wind", {})
    wind_speed = wind_result.get("wind_speed_ms")
    wind_dir = wind_result.get("wind_direction_deg")

    water_type = analysis_result.get("water_type", "river")
    season_info = sub_results.get("season", {})
    season = season_info.get("season", "spring") if isinstance(season_info, dict) else "spring"

    # 风向方位文字
    wind_dir_name = _deg_to_compass(wind_dir) if wind_dir is not None else "无"

    # --- 推荐逻辑 ---
    if wind_speed is not None and wind_speed > 5.0:
        if water_type == "river":
            rec = "下游避风岸"
            reason = f"风速 {wind_speed:.1f} m/s ({wind_dir_name}风), 河流下游避风处鱼聚集"
            alts = ["回水湾（缓流区）", "桥墩下游（结构遮蔽）"]
        elif water_type == "reservoir":
            rec = "迎风岸"
            reason = f"风速 {wind_speed:.1f} m/s ({wind_dir_name}风), 迎风岸浮游生物丰富, 鱼跟随觅食"
            alts = ["铧尖（突出点）", "深浅交界处"]
        else:  # black_pit
            rec = "深水区边缘"
            reason = f"风速 {wind_speed:.1f} m/s, 黑坑深水区鱼更稳定"
            alts = ["增氧机附近", "下风口"]
    elif wind_speed is not None and wind_speed > 2.0:
        rec = "侧风位结构区"
        reason = f"微风 {wind_speed:.1f} m/s, 结构区（水草/树荫/桥墩）鱼藏身"
        alts = ["入水口附近", "回水湾"]
    else:
        if season == "summer":
            rec = "深水区/树荫下"
            reason = "无风+夏季, 鱼避高温趋向深水/阴凉"
        elif season == "winter":
            rec = "向阳避风浅滩"
            reason = "无风+冬季, 鱼趋向向阳温暖浅水"
        else:
            rec = "入水口/草边"
            reason = "无风+春秋, 入水口溶氧高食物多"
        alts = ["铧尖", "深浅交界处"]

    logger.info("position: %s (wind=%.1f m/s %s)", rec, wind_speed or 0, wind_dir_name)

    return {
        "recommendation": rec,
        "reason": reason,
        "alternatives": alts,
        "wind_strategy": _wind_strategy(wind_speed, wind_dir, water_type),
    }


def _deg_to_compass(deg: float) -> str:
    """角度转方位."""
    dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    idx = int((deg + 22.5) / 45) % 8
    return dirs[idx]


def _wind_strategy(
    wind_speed: float | None,
    wind_dir: float | None,
    water_type: str,
) -> str:
    """风策略说明."""
    if wind_speed is None:
        return "无风数据"
    if wind_speed > 10.0:
        return "大风危险, 不建议出钓"
    if wind_speed > 5.0:
        dir_name = _deg_to_compass(wind_dir) if wind_dir is not None else ""
        return f"{dir_name}风较大, 选避风位"
    return "微风适宜, 全位可钓"
