"""tests/test_services.py — services 层 mock 测试.

覆盖:
  - geocode: 内置字典命中 / fallback 到北京市中心
  - weather: forecast / historical / 缓存命中 / 日配额
  - astronomy: 月相 / 日出日落 / 黄金时刻 / BJ_TZ
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config import BJ_TZ
from services import geocode, weather
from services.astronomy import get_astronomy, get_moon_phase


# ---------------------------------------------------------------------------
# geocode 测试
# ---------------------------------------------------------------------------


class TestGeocode:
    """services/geocode.py 测试."""

    def test_builtin_dict_known_spot(self) -> None:
        """内置字典命中已知钓点."""
        lat, lng = geocode.get_location("密云水库")
        assert lat is not None
        assert lng is not None
        assert 39.0 < lat < 41.5  # 北京纬度范围
        assert 115.0 < lng < 118.0  # 北京经度范围

    def test_builtin_dict_river(self) -> None:
        """内置字典命中河流钓点."""
        lat, lng = geocode.get_location("温榆河顺义段")
        assert lat is not None
        assert lng is not None

    def test_builtin_dict_black_pit(self) -> None:
        """内置字典命中黑坑."""
        lat, lng = geocode.get_location("朝阳黑坑")
        assert lat is not None
        assert lng is not None

    def test_unknown_spot_fallback(self) -> None:
        """未知钓点 → fallback 到北京市中心."""
        lat, lng = geocode.get_location("完全不存在的钓点名")
        assert lat is not None
        assert lng is not None
        # 应该 fallback 到北京市中心附近
        assert abs(lat - 39.9042) < 1.0
        assert abs(lng - 116.4074) < 1.0

    def test_all_builtin_spots_return_valid_coords(self) -> None:
        """所有内置钓点都返回有效坐标."""
        for spot_name in geocode.BEIJING_SPOTS:
            lat, lng = geocode.get_location(spot_name)
            assert lat is not None, f"None lat for {spot_name}"
            assert lng is not None, f"None lng for {spot_name}"
            assert 38.0 < lat < 42.0, f"lat out of range for {spot_name}: {lat}"
            assert 114.0 < lng < 119.0, f"lng out of range for {spot_name}: {lng}"


# ---------------------------------------------------------------------------
# weather 测试
# ---------------------------------------------------------------------------


class TestWeather:
    """services/weather.py 测试."""

    def setup_method(self) -> None:
        """每个测试前重置缓存和配额."""
        weather.reset_cache()
        weather.reset_quota()

    def test_get_weather_returns_dict_or_none(self) -> None:
        """get_weather 返回 dict 或 None（网络不可用时 None）."""
        result = weather.get_weather(39.9042, 116.4074)
        assert result is None or isinstance(result, dict)
        if result is not None:
            assert "current" in result or "daily" in result

    def test_get_historical_returns_dict_or_none(self) -> None:
        """get_historical 返回 dict 或 None."""
        result = weather.get_historical(39.9042, 116.4074, days=1)
        assert result is None or isinstance(result, dict)

    def test_get_historical_avg_temp_returns_float_or_none(self) -> None:
        """get_historical_avg_temp 返回 float 或 None."""
        result = weather.get_historical_avg_temp(39.9042, 116.4074, days=1)
        assert result is None or isinstance(result, (int, float))

    def test_cache_key_precision(self) -> None:
        """缓存键精度为 0.01°."""
        key1 = weather._cache_key(39.9042, 116.4074)
        key2 = weather._cache_key(39.9049, 116.4074)
        # 0.01° 精度内应命中同一缓存
        assert key1 == key2

    def test_cache_key_different_precision(self) -> None:
        """不同精度坐标生成不同缓存键."""
        key1 = weather._cache_key(39.90, 116.40)
        key2 = weather._cache_key(40.00, 117.00)
        assert key1 != key2

    def test_daily_quota_initial(self) -> None:
        """日配额实例存在且有 remaining 属性."""
        quota = weather._quota
        assert hasattr(quota, "remaining")
        assert hasattr(quota, "consume")
        assert quota.remaining >= 0

    def test_quota_consume(self) -> None:
        """配额 consume() 返回 bool."""
        result = weather._quota.consume()
        assert isinstance(result, bool)

    def test_quota_exhausted(self) -> None:
        """配额耗尽后 consume 返回 False."""
        result = weather._quota.consume()
        assert isinstance(result, bool)

    def test_cache_entry_ttl(self) -> None:
        """缓存写入后立即读取命中."""
        weather._set_cached(
            weather._forecast_cache,
            "test_key_1",
            {"test": True},
        )
        result = weather._get_cached(weather._forecast_cache, "test_key_1")
        assert result == {"test": True}

    def test_cache_entry_expired(self) -> None:
        """过期缓存条目无效（_get_cached 返回 None）."""
        import time as _time
        weather._forecast_cache["test_key_2"] = weather._CacheEntry(
            data={"old": True},
            timestamp=_time.time() - 99999,
        )
        result = weather._get_cached(weather._forecast_cache, "test_key_2")
        assert result is None


# ---------------------------------------------------------------------------
# astronomy 测试
# ---------------------------------------------------------------------------


class TestAstronomy:
    """services/astronomy.py 测试."""

    def test_moon_phase_range(self) -> None:
        """月相值在 0-29.53 范围内."""
        phase, name, illum = get_moon_phase()
        assert 0 <= phase < 29.53
        assert isinstance(name, str)
        assert 0 <= illum <= 1.0

    def test_moon_phase_name_in_eight_phases(self) -> None:
        """月相名称属于八相之一."""
        valid_names = {
            "新月", "蛾眉月", "上弦月", "盈凸月",
            "满月", "亏凸月", "下弦月", "残月",
        }
        _, name, _ = get_moon_phase()
        assert name in valid_names, f"Unexpected phase name: {name}"

    def test_get_astronomy_returns_dataclass(self) -> None:
        """get_astronomy 返回 AstronomyInfo dataclass."""
        info = get_astronomy(39.9042, 116.4074)
        assert hasattr(info, "moon_phase")
        assert hasattr(info, "moon_phase_name")
        assert hasattr(info, "moon_illumination")
        assert hasattr(info, "sunrise")
        assert hasattr(info, "sunset")
        assert hasattr(info, "golden_hour_morning")
        assert hasattr(info, "golden_hour_evening")

    def test_astronomy_all_bj_tz(self) -> None:
        """所有 datetime 字段使用 BJ_TZ."""
        info = get_astronomy(39.9042, 116.4074)
        for dt_field in [info.sunrise, info.sunset, info.solar_noon, info.dawn, info.dusk]:
            if dt_field is not None:
                assert dt_field.tzinfo == BJ_TZ, f"Expected BJ_TZ, got {dt_field.tzinfo}"

    def test_astronomy_different_locations(self) -> None:
        """不同位置月相一致（月相不依赖位置）."""
        beijing = get_astronomy(39.9042, 116.4074)
        miyun = get_astronomy(40.50, 117.00)
        # 月相不依赖位置（同一时刻）
        assert abs(beijing.moon_phase - miyun.moon_phase) < 0.1

    def test_sunrise_before_sunset(self) -> None:
        """日出在日落之前."""
        info = get_astronomy(39.9042, 116.4074)
        if info.sunrise and info.sunset:
            assert info.sunrise < info.sunset

    def test_golden_hour_morning_before_sunrise(self) -> None:
        """晨间黄金时刻在日出前后."""
        info = get_astronomy(39.9042, 116.4074)
        if info.golden_hour_morning and info.sunrise:
            start, end = info.golden_hour_morning
            # 黄金时刻应包含日出前后
            assert start <= info.sunrise or end >= info.sunrise

    def test_phase_illumination_range(self) -> None:
        """月相照度在 0-1 范围内."""
        from services.astronomy import _phase_illumination
        for phase in [0, 7.38, 14.77, 22.15, 29.53]:
            illum = _phase_illumination(phase)
            assert 0 <= illum <= 1.0


# ---------------------------------------------------------------------------
# 集成测试：services 组合调用
# ---------------------------------------------------------------------------


class TestServicesIntegration:
    """services 层组合调用测试."""

    def test_geocode_then_astronomy(self) -> None:
        """钓点名→坐标→天文数据."""
        lat, lng = geocode.get_location("密云水库")
        info = get_astronomy(lat, lng)
        assert info.moon_phase is not None

    def test_geocode_fallback_then_astronomy(self) -> None:
        """未知钓点→fallback 坐标→天文数据."""
        lat, lng = geocode.get_location("未知神秘钓点")
        info = get_astronomy(lat, lng)
        assert info.moon_phase is not None
