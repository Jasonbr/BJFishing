"""services/weather.py — Open-Meteo 气象数据服务。

双端点策略（v3 修订 #1）：
1. Forecast API (api.open-meteo.com) — 实时预报，未来 3 天
2. Archive API (archive-api.open-meteo.com) — ERA5 历史数据，用于 3 日均温估算

关键设计（v3 修订 #5）：
- 1h TTL 内存缓存：forecast + historical 各自独立缓存，减少重复请求
- 日配额计数器：Open-Meteo 免费 10000 次/天，超限返回 None 降级
- 时区：所有请求 timezone=Asia/Shanghai，与 config.BJ_TZ 一致
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, cast

import requests

from config import BJ_TZ, settings

logger = logging.getLogger(__name__)

# ============================================================================
# Open-Meteo API 请求变量（逗号分隔字符串，符合 API 规范）
# ============================================================================
_CURRENT_VARS: Final[str] = (
    "temperature_2m,"
    "relative_humidity_2m,"
    "precipitation,"
    "wind_speed_10m,"
    "wind_direction_10m,"
    "surface_pressure"
)

_FORECAST_DAILY_VARS: Final[str] = (
    "temperature_2m_max,"
    "temperature_2m_min,"
    "precipitation_sum,"
    "wind_speed_10m_max,"
    "sunrise,"
    "sunset,"
    "uv_index_max"
)

_ARCHIVE_DAILY_VARS: Final[str] = (
    "temperature_2m_mean,"
    "precipitation_sum,"
    "wind_speed_10m_max"
)

_BJ_TIMEZONE: Final[str] = "Asia/Shanghai"


# ============================================================================
# 日配额计数器（v3 修订 #5）— 按日重置，forecast+archive 共享配额
# ============================================================================
@dataclass
class _DailyQuota:
    """Open-Meteo 日配额计数器。

    Open-Meteo 免费额度 10000 次/天（forecast + archive 共享）。
    超限时返回 None，上游降级处理（data_quality=degraded）。
    """
    _date: str = ""
    _count: int = 0

    def consume(self) -> bool:
        """检查配额并递增计数。超限返回 False。"""
        today = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
        if today != self._date:
            self._date = today
            self._count = 0

        if self._count >= settings.openmeteo_daily_quota:
            logger.warning(
                "weather: 日配额超限 %d/%d，降级处理",
                self._count, settings.openmeteo_daily_quota,
            )
            return False
        self._count += 1
        return True

    @property
    def remaining(self) -> int:
        """剩余配额。"""
        today = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
        if today != self._date:
            return settings.openmeteo_daily_quota
        return settings.openmeteo_daily_quota - self._count


_quota = _DailyQuota()


# ============================================================================
# 内存缓存（1h TTL）— 精度 0.01°（约 1km），同区域钓点共享缓存
# ============================================================================
@dataclass
class _CacheEntry:
    """缓存条目，带 TTL 时间戳。"""
    data: dict[str, Any]
    timestamp: float  # time.time() epoch seconds


_forecast_cache: dict[str, _CacheEntry] = {}
_historical_cache: dict[str, _CacheEntry] = {}


def _cache_key(lat: float, lng: float) -> str:
    """生成缓存键（精度 0.01°，约 1km，同区域共享）。"""
    return f"{lat:.2f},{lng:.2f}"


def _get_cached(cache: dict[str, _CacheEntry], key: str) -> dict[str, Any] | None:
    """获取缓存数据，过期则删除并返回 None。"""
    entry = cache.get(key)
    if entry is None:
        return None
    if time.time() - entry.timestamp > settings.weather_cache_ttl:
        logger.debug("weather: 缓存过期 key=%s", key)
        cache.pop(key, None)
        return None
    return entry.data


def _set_cached(cache: dict[str, _CacheEntry], key: str, data: dict[str, Any]) -> None:
    """写入缓存。"""
    cache[key] = _CacheEntry(data=data, timestamp=time.time())


# ============================================================================
# 公开接口
# ============================================================================
def get_weather(lat: float, lng: float) -> dict[str, Any] | None:
    """获取钓点天气预报数据（实时 + 未来 3 天）。

    1h TTL 缓存命中则直接返回，否则请求 Open-Meteo Forecast API。
    日配额超限或请求失败返回 None，上游应降级处理。

    Args:
        lat: 纬度 WGS84
        lng: 经度 WGS84

    Returns:
        Open-Meteo forecast JSON（含 current + daily），或 None

    Examples:
        >>> data = get_weather(39.90, 116.40)
        >>> data["current"]["temperature_2m"]  # 当前温度 ℃
        >>> data["daily"]["temperature_2m_max"]  # 未来 3 天最高温列表
    """
    key = _cache_key(lat, lng)

    cached = _get_cached(_forecast_cache, key)
    if cached is not None:
        logger.debug("weather: forecast 缓存命中 key=%s", key)
        return cached

    if not _quota.consume():
        return None

    try:
        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lng,
            "current": _CURRENT_VARS,
            "daily": _FORECAST_DAILY_VARS,
            "hourly": "surface_pressure",
            "past_hours": 3,
            "timezone": _BJ_TIMEZONE,
            "forecast_days": 3,
            "wind_speed_unit": "ms",
        }
        resp = requests.get(
            settings.openmeteo_forecast_url,
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = cast(dict[str, Any], resp.json())
        _set_cached(_forecast_cache, key, data)
        logger.info(
            "weather: forecast 获取成功 key=%s remaining=%d",
            key, _quota.remaining,
        )
        return data

    except requests.RequestException as e:
        logger.error("weather: forecast 请求失败 key=%s: %s", key, e)
        return None


def get_historical(
    lat: float, lng: float, days: int = 3,
) -> dict[str, Any] | None:
    """获取历史气象数据（用于 3 日均温估算）。

    从 (今天 - days) 到昨天，取过去 days 天的历史数据。
    用于水温推算公式：water_temp = temp_air_3d_avg * 0.75 + 3.5

    ERA5 数据源有 5 天延迟，最近几天可能无数据，调用方应处理 None。

    Args:
        lat: 纬度 WGS84
        lng: 经度 WGS84
        days: 回溯天数（默认 3）

    Returns:
        Open-Meteo archive JSON（含 daily.temperature_2m_mean），或 None
    """
    end = datetime.now(BJ_TZ).date() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    key = f"{_cache_key(lat, lng)},{start},{end}"

    cached = _get_cached(_historical_cache, key)
    if cached is not None:
        logger.debug("weather: historical 缓存命中 key=%s", key)
        return cached

    if not _quota.consume():
        return None

    try:
        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lng,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": _ARCHIVE_DAILY_VARS,
            "timezone": _BJ_TIMEZONE,
        }
        resp = requests.get(
            settings.openmeteo_archive_url,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = cast(dict[str, Any], resp.json())
        _set_cached(_historical_cache, key, data)
        logger.info(
            "weather: historical 获取成功 key=%s remaining=%d",
            key, _quota.remaining,
        )
        return data

    except requests.RequestException as e:
        logger.error("weather: historical 请求失败 key=%s: %s", key, e)
        return None


def get_historical_avg_temp(
    lat: float, lng: float, days: int = 3,
) -> float | None:
    """便捷接口：获取过去 N 天平均气温。

    用于水温推算：water_temp = avg_temp * 0.75 + 3.5（水库 -2，黑坑 +1）

    Args:
        lat: 纬度
        lng: 经度
        days: 回溯天数

    Returns:
        N 日平均气温（℃），或 None（请求失败/无数据）
    """
    data = get_historical(lat, lng, days)
    if data is None:
        return None

    daily = data.get("daily", {})
    temps: list[float | None] = daily.get("temperature_2m_mean", [])

    valid_temps = [t for t in temps if t is not None]
    if not valid_temps:
        logger.warning(
            "weather: historical 无有效温度数据 lat=%s lng=%s", lat, lng,
        )
        return None

    avg = sum(valid_temps) / len(valid_temps)
    logger.info(
        "weather: %d日均温=%.1f℃ lat=%s lng=%s", days, avg, lat, lng,
    )
    return avg


def reset_cache() -> None:
    """清空所有缓存（测试用）。"""
    _forecast_cache.clear()
    _historical_cache.clear()


def reset_quota() -> None:
    """重置日配额计数器（测试用）。"""
    _quota._count = 0
    _quota._date = ""
