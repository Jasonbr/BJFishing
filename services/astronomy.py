"""services/astronomy.py — Astral 封装，天文计算。

显式使用 BJ_TZ = ZoneInfo("Asia/Shanghai")，确保日出日落/月相时间一致。
月相不随位置变化（全球同一天月相相同），但日出日落/黄金时刻依赖坐标。

提供：
- 月相 (phase 0-29.53 + 中文名 + 照度 0-1)
- 日出/日落/正午/晨光始/暮光终
- 黄金时刻（晨/昏，最佳钓鱼时段）
- 月升/月落
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from astral import Observer, SunDirection
from astral.moon import moonset as _moonset
from astral.moon import moonrise as _moonrise
from astral.moon import phase as moon_phase
from astral.sun import golden_hour, sun

from config import BJ_TZ

logger = logging.getLogger(__name__)

# 月相周期（天）
_LUNAR_CYCLE: Final[float] = 29.53059


def _phase_name(phase: float) -> str:
    """月相数值 → 中文名称。

    phase: 0=新月, 7.38=上弦, 14.77=满月, 22.15=下弦
    八相分割，每相约 3.69 天。
    """
    if phase < 1.85 or phase >= 27.68:
        return "新月"
    elif phase < 5.54:
        return "蛾眉月"
    elif phase < 9.23:
        return "上弦月"
    elif phase < 12.93:
        return "盈凸月"
    elif phase < 16.62:
        return "满月"
    elif phase < 20.31:
        return "亏凸月"
    elif phase < 24.0:
        return "下弦月"
    else:
        return "残月"


def _phase_illumination(phase: float) -> float:
    """月相 → 照度（0.0=新月, 1.0=满月）。

    公式: illumination = (1 - cos(2π * phase / lunar_cycle)) / 2
    """
    return (1 - math.cos(2 * math.pi * phase / _LUNAR_CYCLE)) / 2


@dataclass
class AstronomyInfo:
    """天文信息汇总，一次调用获取所有天文数据。

    所有 datetime 字段均为 BJ_TZ 时区（Asia/Shanghai）。
    None 表示该事件当日不发生（极端纬度可能出现，北京不会）。
    """
    # 月相
    moon_phase: float  # 0-29.53
    moon_phase_name: str  # 中文八相名
    moon_illumination: float  # 0.0-1.0

    # 日出日落（BJ_TZ 时区）
    sunrise: datetime | None
    sunset: datetime | None
    solar_noon: datetime | None
    dawn: datetime | None  # 民用晨光始
    dusk: datetime | None  # 民用暮光终

    # 黄金时刻（晨/昏，最佳钓鱼时段）
    golden_hour_morning: tuple[datetime, datetime] | None
    golden_hour_evening: tuple[datetime, datetime] | None

    # 月升月落
    moonrise: datetime | None
    moonset: datetime | None


def get_astronomy(
    lat: float, lng: float, target_date: date | None = None,
) -> AstronomyInfo:
    """获取指定位置和日期的天文信息。

    所有返回的 datetime 均为 BJ_TZ 时区。

    Args:
        lat: 纬度 WGS84
        lng: 经度 WGS84
        target_date: 目标日期（默认今天，BJ_TZ）

    Returns:
        AstronomyInfo dataclass

    Examples:
        >>> info = get_astronomy(39.90, 116.40)
        >>> info.moon_phase_name
        '满月'
        >>> info.sunrise.strftime('%H:%M')
        '05:09'
    """
    if target_date is None:
        target_date = datetime.now(BJ_TZ).date()

    loc = Observer(latitude=lat, longitude=lng, elevation=0.0)

    # --- 月相（与位置无关，仅依赖日期）---
    phase_val = moon_phase(target_date)
    phase_name = _phase_name(phase_val)
    illumination = _phase_illumination(phase_val)

    # --- 日出日落 ---
    sun_info = sun(loc, date=target_date, tzinfo=BJ_TZ)
    sunrise = sun_info.get("sunrise")
    sunset = sun_info.get("sunset")
    solar_noon = sun_info.get("noon")
    dawn = sun_info.get("dawn")
    dusk = sun_info.get("dusk")

    # --- 黄金时刻（晨=日出后, 昏=日落前）---
    try:
        gh_morning = golden_hour(
            loc, date=target_date,
            direction=SunDirection.RISING, tzinfo=BJ_TZ,
        )
    except (ValueError, TypeError) as e:
        logger.warning("astronomy: 晨间黄金时刻计算失败: %s", e)
        gh_morning = None

    try:
        gh_evening = golden_hour(
            loc, date=target_date,
            direction=SunDirection.SETTING, tzinfo=BJ_TZ,
        )
    except (ValueError, TypeError) as e:
        logger.warning("astronomy: 昏间黄金时刻计算失败: %s", e)
        gh_evening = None

    # --- 月升月落 ---
    try:
        mr = _moonrise(loc, date=target_date, tzinfo=BJ_TZ)
    except (ValueError, TypeError) as e:
        logger.debug("astronomy: 月升计算失败: %s", e)
        mr = None

    try:
        ms = _moonset(loc, date=target_date, tzinfo=BJ_TZ)
    except (ValueError, TypeError) as e:
        logger.debug("astronomy: 月落计算失败: %s", e)
        ms = None

    logger.info(
        "astronomy: date=%s phase=%.2f(%s) illum=%.1f%% "
        "sunrise=%s sunset=%s",
        target_date, phase_val, phase_name, illumination * 100,
        sunrise.strftime("%H:%M") if sunrise else "N/A",
        sunset.strftime("%H:%M") if sunset else "N/A",
    )

    return AstronomyInfo(
        moon_phase=phase_val,
        moon_phase_name=phase_name,
        moon_illumination=illumination,
        sunrise=sunrise,
        sunset=sunset,
        solar_noon=solar_noon,
        dawn=dawn,
        dusk=dusk,
        golden_hour_morning=gh_morning,
        golden_hour_evening=gh_evening,
        moonrise=mr,
        moonset=ms,
    )


def get_moon_phase(target_date: date | None = None) -> tuple[float, str, float]:
    """便捷接口：仅获取月相信息（无需坐标）。

    月相全球同一天相同，不依赖位置。

    Args:
        target_date: 目标日期（默认今天）

    Returns:
        (phase, name, illumination) — (0-29.53, 中文名, 0.0-1.0)
    """
    if target_date is None:
        target_date = datetime.now(BJ_TZ).date()

    phase_val = moon_phase(target_date)
    return (
        phase_val,
        _phase_name(phase_val),
        _phase_illumination(phase_val),
    )
