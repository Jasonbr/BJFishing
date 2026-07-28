"""strategy/risk.py — 风险评估策略.

T3.3d: 根据天气/合规状态评估作钓风险.

逻辑:
  - 风速 > 10 m/s: dangerous（大风危险）
  - 风速 6-10 m/s: caution（抛投困难）
  - 降水 > 10mm: caution（涨水/浑浊）
  - 气温 < -10℃: caution（失温风险）
  - 气温 > 35℃: caution（中暑风险）
  - 合规拦截: prohibited（法律风险）
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def assess_risk(analysis_result: dict[str, Any]) -> dict[str, Any]:
    """评估作钓风险.

    Args:
        analysis_result: analyze_fishing() 返回的分析结果

    Returns:
        {level, warnings, safety_tips}
    """
    sub_results = analysis_result.get("sub_results", {})
    conditions = analysis_result.get("conditions", {})
    compliance = analysis_result.get("compliance", {})

    weather = conditions.get("weather", {}) if conditions else {}
    current = weather.get("current", {}) if weather else {}

    wind_result = sub_results.get("wind", {})
    wind_speed = wind_result.get("wind_speed_ms")

    air_temp = _safe_float(current.get("temperature_2m"))
    precip = _safe_float(current.get("precipitation"))

    warnings: list[str] = []
    level = "safe"

    # --- 合规拦截 → prohibited ---
    if compliance.get("block_analysis"):
        level = "prohibited"
        reasons = compliance.get("reasons", [])
        warnings.append(f"禁渔/违规: {', '.join(reasons) if reasons else '合规拦截'}")
        logger.warning("risk: prohibited (compliance block)")
        return {
            "level": level,
            "warnings": warnings,
            "safety_tips": ["请勿出钓, 遵守禁渔规定"],
        }

    # --- 风速评估 ---
    if wind_speed is not None:
        if wind_speed > 10.0:
            level = "dangerous"
            warnings.append(f"大风 {wind_speed:.1f} m/s, 危险不宜出钓")
        elif wind_speed > 6.0:
            if level == "safe":
                level = "caution"
            warnings.append(f"风较大 {wind_speed:.1f} m/s, 抛投困难注意安全")

    # --- 降水评估 ---
    if precip is not None and precip > 10.0:
        if level == "safe":
            level = "caution"
        warnings.append(f"降水 {precip:.1f} mm, 可能涨水浑浊")

    # --- 气温评估 ---
    if air_temp is not None:
        if air_temp < -10.0:
            if level == "safe":
                level = "caution"
            warnings.append(f"严寒 {air_temp:.1f}℃, 注意保暖防失温")
        elif air_temp > 35.0:
            if level == "safe":
                level = "caution"
            warnings.append(f"高温 {air_temp:.1f}℃, 注意防暑防晒")

    # --- 安全提示 ---
    tips: list[str] = []
    if level == "safe":
        tips = ["天气适宜, 注意水域深浅", "携带救生设备"]
    elif level == "caution":
        tips = ["谨慎出钓, 注意安全", "穿戴救生衣", "告知他人行程"]
    elif level == "dangerous":
        tips = ["不建议出钓", "如必须, 请在安全区域"]
    elif level == "prohibited":
        tips = ["请勿出钓, 遵守禁渔规定"]

    logger.info("risk: level=%s warnings=%d", level, len(warnings))

    return {
        "level": level,
        "warnings": warnings,
        "safety_tips": tips,
    }


def _safe_float(val: Any) -> float | None:
    """安全转 float."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
