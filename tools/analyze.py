"""tools/analyze.py — 综合分析鱼情.

T3.2 完整实现:
  - 调 tools/collect.py 采集环境数据
  - 调 engine 8 维评分（气压/温度/月相/风/降水/季节/水温/溶氧）
  - 调 compliance/gate.py 合规拦截（禁渔期/饮用水源→block_analysis=true）
  - 错误降级策略（data_quality=partial 时降权评分，degraded 时跳过评分）
  - 加反馈调权（P4 T4.3）
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from compliance.gate import check_compliance
from engine import oxygen as oxygen_mod
from engine import precipitation as precip_mod
from engine import pressure as pressure_mod
from engine import season as season_mod
from engine import solunar as solunar_mod
from engine import temperature as temp_mod
from engine import water_temp as wt_mod
from engine import wind as wind_mod
from engine.weights import compute_fishing_score, get_weights
from tools.collect import collect_conditions
from feedback.tuning import get_cached_adjustment

from config import BJ_TZ

logger = logging.getLogger(__name__)

# 数据缺失时的中性默认分（避免拉偏总分）
_NEUTRAL_SCORE = 0.5


async def analyze_fishing(
    spot_name: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    water_type: str | None = None,
    species_id: str | None = None,
    feedback_adjustment: float | None = None,
) -> dict[str, Any]:
    """综合分析鱼情.

    Args:
        spot_name: 钓点名称
        lat: 纬度
        lng: 经度
        water_type: 水域类型 (river/reservoir/black_pit)
        species_id: 鱼种ID（默认 None=通用）
        feedback_adjustment: 反馈调权 (±0.10, None=从 tuning 缓存自动读取)

    Returns:
        含 fishing_score / sub_scores / sub_results / compliance / data_quality 的 dict

    data_quality 策略:
        - full: 正常评分
        - partial: 评分但 confidence="low"，总分 *= 0.9
        - degraded: 跳过评分，fishing_score=None
    """
    wtype = water_type or "river"

    # --- 0. 反馈调权（未指定时从 tuning 缓存读取） ---
    if feedback_adjustment is None:
        feedback_adjustment = get_cached_adjustment()

    # --- 1. 采集环境数据 ---
    conditions = await collect_conditions(
        spot_name=spot_name, lat=lat, lng=lng, water_type=wtype,
    )

    data_quality = conditions.get("data_quality", "degraded")
    data_quality_reasons: list[str] = conditions.get("data_quality_reasons", [])

    # --- 2. 降级模式：天气数据获取失败，无法评分 ---
    if data_quality == "degraded":
        logger.warning("analyze: degraded mode, skipping scoring")
        return _degraded_analysis(conditions, data_quality_reasons)

    # --- 3. 提取天气数据 ---
    weather = conditions.get("weather") or {}
    current = weather.get("current") or {}
    daily = weather.get("daily") or {}

    air_temp_c = _safe_float(current.get("temperature_2m"))
    precip_mm = _safe_float(current.get("precipitation"), default=0.0)
    wind_speed_ms = _safe_float(current.get("wind_speed_10m"))
    wind_direction_deg = _safe_float(current.get("wind_direction_10m"))
    current_hpa = _safe_float(current.get("surface_pressure"))

    # 前日降水（用于趋势分析）
    precip_sum = daily.get("precipitation_sum") or []
    prev_precip_mm = _safe_float(
        precip_sum[1] if len(precip_sum) > 1 else None,
    ) if precip_sum else None

    # --- 4. 计算引擎评分 ---
    sub_scores: dict[str, float] = {}
    sub_results: dict[str, Any] = {}

    # 4a. 气压
    if current_hpa is not None:
        pr = pressure_mod.score_pressure(current_hpa)
        sub_scores["pressure"] = pr.score
        sub_results["pressure"] = pr
    else:
        sub_scores["pressure"] = _NEUTRAL_SCORE
        data_quality_reasons.append("气压数据缺失，使用中性分")

    # 4b. 温度（需先估算水温）
    water_temp_result = wt_mod.estimate_water_temp(
        conditions["lat"], conditions["lng"], water_type=wtype,
    )
    sub_results["water_temp"] = water_temp_result
    water_temp_c = water_temp_result.water_temp_c

    if air_temp_c is not None:
        tr = temp_mod.score_temperature(water_temp_c, air_temp_c, species_id)
        sub_scores["temperature"] = tr.score
        sub_results["temperature"] = tr
        # 检查 species_id 是否有效
        if species_id and species_id not in temp_mod._load_species():
            data_quality_reasons.append(f"未知鱼种ID '{species_id}'，使用通用温度评分")
    else:
        sub_scores["temperature"] = _NEUTRAL_SCORE
        data_quality_reasons.append("气温数据缺失，使用中性分")

    # 4c. 月相/天文
    sr = solunar_mod.score_solunar()
    sub_scores["solunar"] = sr.score
    sub_results["solunar"] = sr

    # 4d. 风
    if wind_speed_ms is not None:
        wr = wind_mod.score_wind(wind_speed_ms, wind_direction_deg)
        sub_scores["wind"] = wr.score
        sub_results["wind"] = wr
    else:
        sub_scores["wind"] = _NEUTRAL_SCORE
        data_quality_reasons.append("风数据缺失，使用中性分")

    # 4e. 降水
    precip_val = precip_mm if precip_mm is not None else 0.0
    pr2 = precip_mod.score_precipitation(precip_val, prev_precip_mm)
    sub_scores["precipitation"] = pr2.score
    sub_results["precipitation"] = pr2

    # 4f. 季节
    season_info = season_mod.get_season()
    sub_scores["season"] = _season_score(season_info)
    sub_results["season"] = season_info

    # 4g. 水温 + 溶氧 → 合成 water_score
    ox = oxygen_mod.estimate_oxygen(water_temp_c)
    sub_results["oxygen"] = ox
    quality_factor = _water_quality_factor(water_temp_result.data_quality)
    water_score = ox.score * quality_factor
    sub_scores["water"] = round(water_score, 3)

    # --- 5. 合规检查 ---
    compliance = check_compliance(
        water_id=spot_name,
        water_type=wtype,
        fishing_date=None,
        gear_id=None,
    )

    # --- 5b. 合规拦截 → 不返回 fishing_score ---
    if compliance.block_analysis:
        logger.warning(
            "analyze: blocked by compliance: %s", compliance.reasons,
        )
        return {
            "spot_name": spot_name,
            "lat": conditions["lat"],
            "lng": conditions["lng"],
            "water_type": wtype,
            "fishing_score": None,
            "sub_scores": sub_scores,
            "sub_results": _serialize_results(sub_results),
            "compliance": compliance.to_dict(),
            "conditions": _serialize_conditions(conditions),
            "data_quality": data_quality,
            "data_quality_reasons": data_quality_reasons,
            "confidence": "blocked",
            "blocked": True,
            "blocked_reason": "; ".join(compliance.reasons) if compliance.reasons else "合规拦截",
            "weights_used": {},
            "analyzed_at": datetime.now(BJ_TZ).isoformat(),
        }

    # --- 6. 计算总分 ---
    weights = get_weights(season_info.season)
    fishing_score = compute_fishing_score(
        pressure_score=sub_scores["pressure"],
        temperature_score=sub_scores["temperature"],
        solunar_score=sub_scores["solunar"],
        wind_score=sub_scores["wind"],
        precipitation_score=sub_scores["precipitation"],
        season_score=sub_scores["season"],
        water_score=sub_scores["water"],
        feedback_adjustment=feedback_adjustment,
        weights=weights,
    )

    # --- 7. 降级调整 ---
    confidence = "high"
    if data_quality == "partial":
        confidence = "low"
        fishing_score = round(fishing_score * 0.9, 3)
        logger.info(
            "analyze: partial data, score reduced to %.3f", fishing_score,
        )

    logger.info(
        "analyze: spot=%s score=%.3f quality=%s confidence=%s",
        spot_name or f"({conditions['lat']},{conditions['lng']})",
        fishing_score, data_quality, confidence,
    )

    return {
        "spot_name": spot_name,
        "lat": conditions["lat"],
        "lng": conditions["lng"],
        "water_type": wtype,
        "fishing_score": fishing_score,
        "sub_scores": sub_scores,
        "sub_results": _serialize_results(sub_results),
        "compliance": compliance.to_dict(),
        "conditions": _serialize_conditions(conditions),
        "data_quality": data_quality,
        "data_quality_reasons": data_quality_reasons,
        "confidence": confidence,
        "weights_used": {
            "pressure": weights.pressure,
            "temperature": weights.temperature,
            "solunar": weights.solunar,
            "wind": weights.wind,
            "precipitation": weights.precipitation,
            "season": weights.season,
            "water": weights.water,
        },
        "analyzed_at": datetime.now(BJ_TZ).isoformat(),
    }


# ---------- 辅助函数 ----------


def _safe_float(val: Any, default: float | None = None) -> float | None:
    """安全转 float, 失败返回 default."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _season_score(season_info: Any) -> float:
    """从 SeasonInfo 提取/推导评分.

    SeasonInfo 没有直接 score 字段, 用季节适宜性推导:
      spring/autumn: 0.8 (最佳)
      summer: 0.5 (偏热)
      winter: 0.3 (偏冷)
    """
    season = getattr(season_info, "season", "spring")
    return {"spring": 0.8, "summer": 0.5, "autumn": 0.8, "winter": 0.3}.get(
        season, 0.5,
    )


def _water_quality_factor(data_quality: str) -> float:
    """水温估算质量 → water_score 置信因子."""
    return {"full": 1.0, "partial": 0.85, "degraded": 0.7}.get(
        data_quality, 0.7,
    )


def _serialize_results(sub_results: dict[str, Any]) -> dict[str, Any]:
    """将 dataclass 结果转为可序列化 dict."""
    out: dict[str, Any] = {}
    for key, val in sub_results.items():
        if hasattr(val, "__dataclass_fields__"):
            out[key] = {
                k: _serialize_value(getattr(val, k))
                for k in val.__dataclass_fields__
            }
        else:
            out[key] = _serialize_value(val)
    return out


def _serialize_value(val: Any) -> Any:
    """递归序列化（处理 datetime/date 对象）."""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    return val


def _serialize_conditions(conditions: dict[str, Any]) -> dict[str, Any]:
    """序列化 conditions dict（astronomy 对象等）."""
    out: dict[str, Any] = {}
    for key, val in conditions.items():
        if key == "astronomy" and val is not None:
            if hasattr(val, "__dataclass_fields__"):
                out[key] = {
                    k: _serialize_value(getattr(val, k))
                    for k in val.__dataclass_fields__
                }
            else:
                out[key] = _serialize_value(val)
        elif key == "weather":
            out[key] = val  # 已经是 dict
        elif hasattr(val, "isoformat"):
            out[key] = val.isoformat()
        else:
            out[key] = _serialize_value(val)
    return out


def _degraded_analysis(
    conditions: dict[str, Any], reasons: list[str],
) -> dict[str, Any]:
    """降级模式响应（天气获取失败）."""
    return {
        "spot_name": conditions.get("spot_name"),
        "lat": conditions.get("lat"),
        "lng": conditions.get("lng"),
        "water_type": conditions.get("water_type", "river"),
        "fishing_score": None,
        "sub_scores": {},
        "sub_results": {},
        "compliance": {
            "block_analysis": False,
            "reasons": [],
            "compliance_notes": [],
        },
        "conditions": _serialize_conditions(conditions),
        "data_quality": "degraded",
        "data_quality_reasons": reasons,
        "confidence": "none",
        "weights_used": {},
        "analyzed_at": datetime.now(BJ_TZ).isoformat(),
    }
