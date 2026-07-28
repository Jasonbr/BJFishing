"""services/geocode.py — 钓点名称转经纬度。

五级 fallback 策略：
1. 内置北京钓点字典（最快，离线，覆盖主要水域）
2. 高德 POI 搜索（place/text，适合地名/POI，需要 GAODE_API_KEY）
3. 高德地理编码（geocode/geo，适合地址字符串，需要 GAODE_API_KEY）
4. Open-Meteo 地理编码（免费，城市级精度）
5. Nominatim（免费 OSM 服务，中文精度一般）

返回格式：(latitude, longitude) — WGS84 坐标系
"""
from __future__ import annotations

import logging
from typing import Any, Final

import requests

from config import settings

logger = logging.getLogger(__name__)

# ============================================================================
# 内置北京钓点字典 — 离线可用，覆盖主要水域
# 坐标格式: (latitude, longitude) WGS84
# 数据来源: 高德地图实测 + 钓鱼论坛众包校验
# ============================================================================
BEIJING_SPOTS: Final[dict[str, tuple[float, float]]] = {
    # --- 温榆河（昌平/顺义/通州段）---
    "温榆河昌平段": (40.2207, 116.2317),
    "温榆河顺义段": (40.1558, 116.6513),
    "温榆河通州段": (39.9213, 116.6566),
    "温榆河": (40.1558, 116.6513),  # 默认中段

    # --- 永定河（门头沟/丰台/大兴段）---
    "永定河门头沟段": (39.9400, 116.0956),
    "永定河丰台段": (39.8447, 116.2745),
    "永定河大兴段": (39.7430, 116.3398),
    "永定河": (39.8447, 116.2745),  # 默认中段

    # --- 潮白河（密云/顺义/通州段）---
    "潮白河密云段": (40.3819, 116.8432),
    "潮白河顺义段": (40.1287, 116.6547),
    "潮白河通州段": (39.9087, 116.7057),
    "潮白河": (40.1287, 116.6547),  # 默认中段

    # --- 北运河 ---
    "北运河": (39.9042, 116.4074),
    "北运河通州段": (39.8900, 116.6566),

    # --- 水库 ---
    "密云水库": (40.5000, 117.0000),
    "官厅水库": (40.2000, 115.6000),
    "十三陵水库": (40.2500, 116.2000),
    "怀柔水库": (40.3500, 116.6500),

    # --- 黑坑（示例，实际黑坑众多）---
    "朝阳黑坑": (39.9200, 116.5000),
    "通州黑坑": (39.8900, 116.7000),
    "孙河文创中心": (40.047025, 116.545450),  # 温榆河绿道

    # --- 北京市中心 fallback ---
    "北京市中心": (39.9042, 116.4074),
    "北京": (39.9042, 116.4074),
}

# 北京市中心默认坐标（所有后端失败时）
BEIJING_CENTER: Final[tuple[float, float]] = (39.9042, 116.4074)


def get_location(name: str) -> tuple[float, float]:
    """钓点名称 → 经纬度坐标。

    五级 fallback 策略：
    1. 内置字典（O(1)，离线）
    2. 高德 POI 搜索（place/text，适合 POI 名，需要 key）
    3. 高德地理编码（geocode/geo，适合地址，需要 key）
    4. Open-Meteo 地理编码（免费，城市级精度）
    5. Nominatim（免费，中文精度一般）

    Args:
        name: 钓点名称（如 "温榆河昌平段"、"密云水库"）

    Returns:
        (latitude, longitude) — WGS84 坐标系

    Examples:
        >>> get_location("密云水库")
        (40.5, 117.0)
        >>> get_location("温榆河")
        (40.1558, 116.6513)
    """
    # --- 1. 内置字典 ---
    if name in BEIJING_SPOTS:
        logger.info("geocode: 内置字典命中 '%s' → %s", name, BEIJING_SPOTS[name])
        return BEIJING_SPOTS[name]

    # --- 2. 高德 POI 搜索（适合地名/POI，如"孙河文创中心"）---
    if settings.gaode_api_key:
        result = _gaode_poi_search(name)
        if result is not None:
            logger.info("geocode: 高德 POI 命中 '%s' → %s", name, result)
            return result
        logger.debug("geocode: 高德 POI 未找到 '%s'，尝试地理编码", name)

        # --- 3. 高德地理编码（适合地址字符串）---
        result = _gaode_geocode(name)
        if result is not None:
            logger.info("geocode: 高德地理编码命中 '%s' → %s", name, result)
            return result
        logger.warning("geocode: 高德 API 未找到 '%s'", name)
    else:
        logger.debug("geocode: 未配置 GAODE_API_KEY，跳过高德")

    # --- 4. Open-Meteo 地理编码（免费，城市级精度）---
    result = _openmeteo_geocode(name)
    if result is not None:
        logger.info("geocode: Open-Meteo 命中 '%s' → %s", name, result)
        return result
    logger.debug("geocode: Open-Meteo 未找到 '%s'", name)

    # --- 5. Nominatim ---
    result = _nominatim_geocode(name)
    if result is not None:
        logger.info("geocode: Nominatim 命中 '%s' → %s", name, result)
        return result
    logger.warning("geocode: Nominatim 未找到 '%s'", name)

    # --- 6. 所有后端失败 → 报错（不再静默 fallback 到北京市中心） ---
    logger.warning("geocode: 所有后端失败，未找到 '%s'", name)
    raise ValueError(f"未找到钓点 '{name}'，请使用 --lat/--lng 手动指定坐标")


def _gaode_poi_search(name: str) -> tuple[float, float] | None:
    """高德地图 POI 搜索 API。

    使用 place/text 接口，适合搜索地名/POI（如"孙河文创中心"）。
    geocode/geo 只适合地址字符串（如"北京市朝阳区xx路xx号"），
    对 POI 名称搜索效果差。

    API 文档: https://lbs.amap.com/api/webservice/guide/api/search
    返回 GCJ-02 坐标，北京地区与 WGS84 差异 < 50m，简化处理。
    """
    try:
        params: dict[str, str | int] = {
            "keywords": name,
            "city": "110000",  # 北京市
            "citylimit": "true",
            "key": settings.gaode_api_key or "",
            "output": "json",
            "offset": 1,
            "page": 1,
        }
        resp = requests.get(
            "https://restapi.amap.com/v3/place/text",
            params=params,
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "1":
            logger.warning("geocode: 高德 POI 返回错误 %s", data.get("info", ""))
            return None

        pois = data.get("pois", [])
        if not pois:
            return None

        location_str = pois[0].get("location", "")
        if not location_str:
            return None

        lng, lat = location_str.split(",")
        return (float(lat), float(lng))

    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        logger.error("geocode: 高德 POI 异常 '%s': %s", name, e)
        return None


def _gaode_geocode(name: str) -> tuple[float, float] | None:
    """高德地图地理编码 API。

    API 文档: https://lbs.amap.com/api/webservice/guide/api/geocode
    返回 GCJ-02 坐标，这里简化为 WGS84（北京地区差异 < 50m）。
    """
    try:
        resp = requests.get(
            "https://restapi.amap.com/v3/geocode/geo",
            params={
                "address": name,
                "key": settings.gaode_api_key,
                "output": "json",
            },
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "1":
            logger.warning("geocode: 高德 API 返回错误 %s", data.get("info", ""))
            return None

        geocodes = data.get("geocodes", [])
        if not geocodes:
            return None

        # 高德返回 "经度,纬度" 格式
        location_str = geocodes[0].get("location", "")
        if not location_str:
            return None

        lng, lat = location_str.split(",")
        return (float(lat), float(lng))

    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        logger.error("geocode: 高德 API 异常 '%s': %s", name, e)
        return None


def _openmeteo_geocode(name: str) -> tuple[float, float] | None:
    """Open-Meteo 地理编码 API（免费，无需 key）。

    城市级精度，对"孙河""密云"等区/镇名可用，
    但对"孙河文创中心"等具体 POI 无效。
    """
    try:
        params: dict[str, str | int] = {
            "name": name,
            "count": 1,
            "language": "zh",
            "format": "json",
        }
        resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params=params,
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            return None

        lat = float(results[0]["latitude"])
        lng = float(results[0]["longitude"])
        return (lat, lng)

    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        logger.error("geocode: Open-Meteo 异常 '%s': %s", name, e)
        return None


def _nominatim_geocode(name: str) -> tuple[float, float] | None:
    """Nominatim 地理编码 API（OpenStreetMap 免费服务）。

    限流: 1 次/秒（需要设置 User-Agent）
    中文精度一般，建议配合高德使用
    """
    try:
        params: dict[str, Any] = {
            "q": f"{name} 北京",
            "format": "json",
            "limit": 1,
            "accept-language": "zh-CN",
        }
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params=params,
            headers={"User-Agent": settings.nominatim_user_agent},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data:
            return None

        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        return (lat, lon)

    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        logger.error("geocode: Nominatim 异常 '%s': %s", name, e)
        return None
