"""strategy/bait.py — 饵料推荐策略.

T3.3b: 根据水温/季节/鱼种推荐饵料.

逻辑:
  - 水温 < 10℃: 动物饵（蚯蚓/红虫）— 低温蛋白需求
  - 水温 10-20℃: 荤素搭配
  - 水温 > 20℃: 植物饵（玉米/面饵）— 高温清淡
  - 季节微调
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 鱼种偏好饵料（默认通用）
_SPECIES_BAIT: dict[str, list[str]] = {
    "carp": ["玉米", "红薯", "颗粒饵"],
    "crucian": ["红虫", "蚯蚓", "商品饵"],
    "grass_carp": ["嫩草", "玉米", "芦苇芯"],
    "bream": ["蚯蚓", "商品饵", "玉米"],
}


def recommend_bait(analysis_result: dict[str, Any]) -> dict[str, Any]:
    """推荐饵料.

    Args:
        analysis_result: analyze_fishing() 返回的分析结果

    Returns:
        {primary, secondary, reason, species_note}
    """
    sub_results = analysis_result.get("sub_results", {})
    water_temp_result = sub_results.get("water_temp", {})
    water_temp_c = water_temp_result.get("water_temp_c", 15.0)

    season_info = sub_results.get("season", {})
    season = season_info.get("season", "spring") if isinstance(season_info, dict) else "spring"

    # --- 按水温推荐 ---
    if water_temp_c < 10.0:
        primary = "红虫"
        secondary = ["蚯蚓", "腥味商品饵"]
        reason = f"水温 {water_temp_c:.1f}℃ 偏低, 鱼需高蛋白, 动物饵效果好"
    elif water_temp_c < 20.0:
        primary = "蚯蚓+玉米"
        secondary = ["腥香商品饵", "红虫"]
        reason = f"水温 {water_temp_c:.1f}℃ 适宜, 荤素搭配诱鱼"
    else:
        primary = "玉米"
        secondary = ["面饵", "薯味商品饵", "嫩草"]
        reason = f"水温 {water_temp_c:.1f}℃ 偏高, 鱼喜清淡, 植物饵优"

    # --- 季节微调 ---
    if season == "winter" and "红虫" not in primary:
        secondary.insert(0, "红虫")
    elif season == "summer" and "玉米" not in primary:
        secondary.insert(0, "玉米")

    # --- 鱼种偏好 ---
    species_note = "通用推荐（未指定鱼种）"

    logger.info("bait: %s (T=%.1f℃ %s)", primary, water_temp_c, season)

    return {
        "primary": primary,
        "secondary": secondary[:3],
        "reason": reason,
        "species_note": species_note,
        "water_temp_c": round(water_temp_c, 1),
    }
