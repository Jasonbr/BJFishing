"""tools/collect.py — 采集钓点环境数据.

T3.1 完整实现:
  - 调 services/geocode.py: 钓点名→坐标
  - 调 services/weather.py: forecast + historical N日均温
  - 调 services/astronomy.py: 月相/日出日落/黄金时刻
  - 数据完整性校验 + data_quality 字段 (full/partial/degraded)

data_quality 规则:
  - full: 天气(current+daily) + 天文 + 历史均温 全部成功
  - partial: 核心天气(current)成功，但 daily/天文/历史均温 部分缺失
  - degraded: 天气获取失败（无法评分）
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from feedback.storage import fetch_by_spot, fetch_recent, to_dict_list

from services.astronomy import get_astronomy
from services.geocode import get_location
from services.weather import get_historical_avg_temp, get_weather

from config import BJ_TZ

logger = logging.getLogger(__name__)


async def collect_conditions(
    spot_name: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    historical_days: int = 3,
    water_type: str = "river",
) -> dict[str, Any]:
    """采集钓点环境数据.

    Args:
        spot_name: 钓点名称（与 lat/lng 二选一）
        lat: 纬度
        lng: 经度
        historical_days: 历史均温回溯天数
        water_type: 水域类型 (river/reservoir/black_pit)

    Returns:
        含 weather / astronomy / historical_avg_temp / data_quality 的 dict

    data_quality:
        - "full": 所有数据源成功
        - "partial": 核心天气成功，部分辅助数据缺失
        - "degraded": 天气获取失败，无法评分
    """
    data_quality_reasons: list[str] = []

    # --- 1. 坐标解析 ---
    if lat is None or lng is None:
        if spot_name is None:
            data_quality_reasons.append("缺少钓点名称和坐标")
            return _degraded_result(
                spot_name, lat, lng, historical_days, water_type,
                data_quality_reasons,
            )
        try:
            lat, lng = get_location(spot_name)
            logger.info("geocode resolved: %s → (%.4f, %.4f)", spot_name, lat, lng)
        except Exception as exc:
            logger.warning("geocode failed for %s: %s", spot_name, exc)
            data_quality_reasons.append(f"geocode失败: {exc}")
            return _degraded_result(
                spot_name, lat, lng, historical_days, water_type,
                data_quality_reasons,
            )

    # --- 2. 天气数据 (current + daily) ---
    weather_data: dict[str, Any] | None = None
    try:
        weather_data = get_weather(lat, lng)
        if weather_data is None:
            data_quality_reasons.append("天气API返回None（配额耗尽或网络失败）")
            return _degraded_result(
                spot_name, lat, lng, historical_days, water_type,
                data_quality_reasons,
            )
    except Exception as exc:
        logger.warning("weather fetch failed: %s", exc)
        data_quality_reasons.append(f"天气获取异常: {exc}")
        return _degraded_result(
            spot_name, lat, lng, historical_days, water_type,
            data_quality_reasons,
        )

    current = weather_data.get("current") or {}
    daily = weather_data.get("daily") or {}

    if not current:
        data_quality_reasons.append("天气current数据缺失")
        return _degraded_result(
            spot_name, lat, lng, historical_days, water_type,
            data_quality_reasons,
        )

    if not daily:
        data_quality_reasons.append("天气daily数据缺失（无趋势分析）")

    # --- 3. 天文数据 (月相/日出日落/黄金时刻) ---
    astronomy_info: Any = None
    try:
        astronomy_info = get_astronomy(lat, lng)
    except Exception as exc:
        logger.warning("astronomy fetch failed: %s", exc)
        data_quality_reasons.append(f"天文数据获取失败: {exc}")

    # --- 4. 历史均温 (用于水温估算) ---
    historical_avg_temp: float | None = None
    try:
        historical_avg_temp = get_historical_avg_temp(
            lat, lng, days=historical_days,
        )
        if historical_avg_temp is None:
            data_quality_reasons.append("历史均温返回None")
    except Exception as exc:
        logger.warning("historical temp fetch failed: %s", exc)
        data_quality_reasons.append(f"历史均温获取失败: {exc}")

    # --- 5. 数据完整性评估 ---
    if not data_quality_reasons:
        data_quality = "full"
    elif current:
        data_quality = "partial"
    else:
        data_quality = "degraded"

    logger.info(
        "collect done: spot=%s quality=%s reasons=%d",
        spot_name or f"({lat},{lng})", data_quality, len(data_quality_reasons),
    )

    # --- 6. 最近渔获自动填充（T4.4） ---
    recent_catches: list[dict[str, Any]] = []
    try:
        if spot_name:
            records = fetch_by_spot(spot_name, limit=5)
        else:
            records = fetch_recent(limit=5)
        recent_catches = to_dict_list(records)
        if recent_catches:
            logger.info("collect: loaded %d recent catches for autofill", len(recent_catches))
    except Exception as exc:
        logger.warning("collect: recent_catch autofill failed: %s", exc)


    # 序列化 astronomy dataclass → dict（避免 json.dumps default=str 输出 repr）
    astronomy_serialized: Any = None
    if astronomy_info is not None and hasattr(astronomy_info, "__dataclass_fields__"):
        astronomy_serialized = {
            k: _serialize_value(getattr(astronomy_info, k))
            for k in astronomy_info.__dataclass_fields__
        }
    elif astronomy_info is not None:
        astronomy_serialized = astronomy_info

    return {
        "spot_name": spot_name,
        "lat": lat,
        "lng": lng,
        "water_type": water_type,
        "weather": weather_data,
        "astronomy": astronomy_serialized,
        "historical_avg_temp": historical_avg_temp,
        "historical_days": historical_days,
        "data_quality": data_quality,
        "data_quality_reasons": data_quality_reasons,
        "recent_catches": recent_catches,
        "collected_at": datetime.now(BJ_TZ),
    }


def _degraded_result(
    spot_name: str | None,
    lat: float | None,
    lng: float | None,
    historical_days: int,
    water_type: str,
    reasons: list[str],
) -> dict[str, Any]:
    """构造降级响应（天气获取失败时）."""
    return {
        "spot_name": spot_name,
        "lat": lat,
        "lng": lng,
        "water_type": water_type,
        "weather": None,
        "astronomy": None,
        "historical_avg_temp": None,
        "historical_days": historical_days,
        "data_quality": "degraded",
        "data_quality_reasons": reasons,
        "recent_catches": [],
        "collected_at": datetime.now(BJ_TZ),
    }


def _serialize_value(val: Any) -> Any:
    """递归序列化（处理 datetime/date 对象）."""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    return val
