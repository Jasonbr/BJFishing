"""tests/test_engine.py — engine 层边界 case 测试.

覆盖:
  - season: 12 季节边界 case（春首末/夏首末/秋首末/冬跨年/闰年）
  - water_temp: 3 水域类型 + 3 级降级策略
  - oxygen: 溶氧公式 + 5 级评分
  - pressure: 正常/稳定/上升/下降/骤降
  - temperature: 通用+鱼种+极端气温
  - solunar: 新月/满月/上弦/下弦/其他
  - wind: 无风/微风/中风/大风/狂风/暴风
  - precipitation: 无雨~大暴雨 + 雨后转晴窗口
  - weights: 默认权重 + 4 季调整 + feedback 钳制 + 归一化
  - 全管道集成测试
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from engine import (
    oxygen,
    precipitation,
    pressure,
    season,
    solunar,
    temperature,
    water_temp,
    weights,
    wind,
)


# ---------------------------------------------------------------------------
# season.py 测试
# ---------------------------------------------------------------------------


class TestSeason:
    """engine/season.py — 季节判断边界 case."""

    def test_spring_first_day(self) -> None:
        """3.1 → spring（首日）."""
        si = season.get_season(date(2026, 3, 1))
        assert si.season == "spring"
        assert si.name == "春季"
        assert 0.8 <= si.scoring_coefficient <= 1.0

    def test_spring_last_day(self) -> None:
        """5.31 → spring（末日）."""
        si = season.get_season(date(2026, 5, 31))
        assert si.season == "spring"

    def test_summer_first_day(self) -> None:
        """6.1 → summer（首日）."""
        si = season.get_season(date(2026, 6, 1))
        assert si.season == "summer"
        assert si.scoring_coefficient < 0.7  # 夏季系数低

    def test_summer_mid(self) -> None:
        """6.30 → summer."""
        si = season.get_season(date(2026, 6, 30))
        assert si.season == "summer"

    def test_summer_last_day(self) -> None:
        """8.31 → summer（末日）."""
        si = season.get_season(date(2026, 8, 31))
        assert si.season == "summer"

    def test_autumn_first_day(self) -> None:
        """9.1 → autumn（首日）."""
        si = season.get_season(date(2026, 9, 1))
        assert si.season == "autumn"
        assert si.scoring_coefficient >= 0.9  # 秋季系数最高

    def test_autumn_last_day(self) -> None:
        """11.15 → autumn（末日）."""
        si = season.get_season(date(2026, 11, 15))
        assert si.season == "autumn"

    def test_winter_first_day(self) -> None:
        """11.16 → winter（首日）."""
        si = season.get_season(date(2026, 11, 16))
        assert si.season == "winter"
        assert si.scoring_coefficient <= 0.4

    def test_winter_end_of_year(self) -> None:
        """12.31 → winter."""
        si = season.get_season(date(2026, 12, 31))
        assert si.season == "winter"

    def test_winter_cross_year(self) -> None:
        """1.1 → winter（跨年）."""
        si = season.get_season(date(2026, 1, 1))
        assert si.season == "winter"

    def test_winter_common_year_feb28(self) -> None:
        """2.28 → winter（平年）."""
        si = season.get_season(date(2025, 2, 28))  # 2025 是平年
        assert si.season == "winter"

    def test_winter_leap_year_feb29(self) -> None:
        """2.29 → winter（闰年 2024）."""
        si = season.get_season(date(2024, 2, 29))  # 2024 是闰年
        assert si.season == "winter"

    def test_leap_year_true(self) -> None:
        """闰年判断: 2024 是闰年."""
        assert season.is_leap_year(2024) is True

    def test_leap_year_false(self) -> None:
        """闰年判断: 2025 不是闰年."""
        assert season.is_leap_year(2025) is False

    def test_leap_year_century(self) -> None:
        """闰年判断: 2000 是闰年（400 整除）."""
        assert season.is_leap_year(2000) is True

    def test_leap_year_century_false(self) -> None:
        """闰年判断: 1900 不是闰年（100 整除但 400 不整除）."""
        assert season.is_leap_year(1900) is False

    def test_get_season_with_datetime(self) -> None:
        """datetime 输入也能正确判断."""
        from config import BJ_TZ

        dt = datetime(2026, 7, 15, 12, 0, tzinfo=BJ_TZ)
        si = season.get_season(dt)
        assert si.season == "summer"


# ---------------------------------------------------------------------------
# oxygen.py 测试
# ---------------------------------------------------------------------------


class TestOxygen:
    """engine/oxygen.py — 溶氧推算."""

    def test_cold_water_high_oxygen(self) -> None:
        """低温水溶氧高（5°C → ~12.8mg/L）."""
        r = oxygen.estimate_oxygen(5.0)
        assert r.dissolved_o2_mg_l > 12.0
        assert r.score == 1.0
        assert r.level == "excellent"

    def test_moderate_water(self) -> None:
        """中温水（15°C → ~11.0mg/L）."""
        r = oxygen.estimate_oxygen(15.0)
        assert r.dissolved_o2_mg_l > 10.0
        assert r.score == 1.0
        assert r.level == "excellent"

    def test_warm_water(self) -> None:
        """温水（25°C → ~9.6mg/L）."""
        r = oxygen.estimate_oxygen(25.0)
        assert 9.0 < r.dissolved_o2_mg_l < 10.0
        assert r.score == 1.0
        assert r.level == "excellent"

    def test_hot_water_low_oxygen(self) -> None:
        """高温水（35°C → ~10.4mg/L，仍 excellent）."""
        r = oxygen.estimate_oxygen(35.0)
        assert r.dissolved_o2_mg_l > 8.0  # 仍 >=8
        assert r.level == "excellent"

    def test_extreme_hot(self) -> None:
        """极端高温（40°C）."""
        r = oxygen.estimate_oxygen(40.0)
        # 14.6 - 0.4*40 + 0.008*1600 = 14.6 - 16 + 12.8 = 11.4
        assert r.dissolved_o2_mg_l > 8.0
        assert r.level == "excellent"

    def test_formula_accuracy(self) -> None:
        """验证公式: 14.6 - 0.4*T + 0.008*T²."""
        for t in [0, 5, 10, 15, 20, 25, 30]:
            expected = 14.6 - 0.4 * t + 0.008 * (t ** 2)
            r = oxygen.estimate_oxygen(float(t))
            assert abs(r.dissolved_o2_mg_l - round(expected, 2)) < 0.01


# ---------------------------------------------------------------------------
# pressure.py 测试
# ---------------------------------------------------------------------------


class TestPressure:
    """engine/pressure.py — 气压评分."""

    def test_normal_stable(self) -> None:
        """正常气压+稳定 → 1.0."""
        r = pressure.score_pressure(1013.0, prev_hpa=1013.5)
        assert r.trend == "stable"
        assert r.score == 1.0

    def test_normal_rising(self) -> None:
        """正常气压+上升 → 1.0+0.2 钳制到 1.0."""
        r = pressure.score_pressure(1015.0, prev_hpa=1012.0)
        assert r.trend == "rising"
        assert r.score == 1.0  # 钳制

    def test_normal_falling(self) -> None:
        """正常气压+微降 → 1.0-0.1=0.9."""
        r = pressure.score_pressure(1012.0, prev_hpa=1013.0)
        assert r.trend == "falling"
        assert abs(r.score - 0.9) < 0.05

    def test_falling_significant(self) -> None:
        """气压下降 2hPa → 1.0-0.3=0.7."""
        r = pressure.score_pressure(1011.0, prev_hpa=1013.0)
        assert r.trend == "falling"
        assert abs(r.score - 0.7) < 0.05

    def test_falling_crash(self) -> None:
        """气压骤降 5hPa → 1.0-0.5=0.5."""
        r = pressure.score_pressure(1008.0, prev_hpa=1013.0)
        assert r.trend == "falling"
        assert abs(r.score - 0.5) < 0.05

    def test_edge_range(self) -> None:
        """边缘气压范围 995-1030 → 0.7 基础分."""
        r = pressure.score_pressure(996.0)
        assert r.trend == "unknown"
        assert abs(r.score - 0.6) < 0.05  # 0.7 - 0.1 unknown

    def test_abnormal_low(self) -> None:
        """气压异常低 <995 → 0.3."""
        r = pressure.score_pressure(990.0)
        assert r.score <= 0.3

    def test_abnormal_high(self) -> None:
        """气压异常高 >1030 → 0.3."""
        r = pressure.score_pressure(1035.0)
        assert r.score <= 0.3

    def test_no_prev(self) -> None:
        """无前序气压 → trend=unknown."""
        r = pressure.score_pressure(1013.0)
        assert r.trend == "unknown"


# ---------------------------------------------------------------------------
# temperature.py 测试
# ---------------------------------------------------------------------------


class TestTemperature:
    """engine/temperature.py — 温度评分."""

    def test_generic_optimal(self) -> None:
        """通用最适 15-25°C → 1.0."""
        r = temperature.score_temperature(20.0)
        assert r.score == 1.0
        assert r.level == "optimal"

    def test_generic_feeding(self) -> None:
        """通用可摄食 10-30°C → 0.7."""
        r = temperature.score_temperature(12.0)
        assert r.score == 0.7
        assert r.level == "feeding"

    def test_generic_edge(self) -> None:
        """通用边缘 4-35°C → 0.4."""
        r = temperature.score_temperature(33.0)
        assert r.score == 0.4
        assert r.level == "feeding"

    def test_generic_extreme(self) -> None:
        """极端水温 → 0.1."""
        r = temperature.score_temperature(2.0)
        assert r.score == 0.1
        assert r.level == "outside"

    def test_species_crucian_optimal(self) -> None:
        """鲫鱼最适 15-25°C → 1.0."""
        r = temperature.score_temperature(20.0, species_id="crucian_carp")
        assert r.score == 1.0
        assert r.level == "optimal"

    def test_species_crucian_feeding(self) -> None:
        """鲫鱼可摄食但非最适 → 0.6."""
        r = temperature.score_temperature(28.0, species_id="crucian_carp")
        assert r.score == 0.6
        assert r.level == "feeding"

    def test_species_crucian_outside(self) -> None:
        """鲫鱼超出摄食范围 → 0.1."""
        r = temperature.score_temperature(35.0, species_id="crucian_carp")
        assert r.score == 0.1
        assert r.level == "outside"

    def test_species_carp_optimal(self) -> None:
        """鲤鱼最适 18-28°C → 1.0."""
        r = temperature.score_temperature(23.0, species_id="common_carp")
        assert r.score == 1.0

    def test_unknown_species_fallback_generic(self) -> None:
        """未知鱼种 → 通用评分."""
        r = temperature.score_temperature(20.0, species_id="nonexistent_fish")
        assert r.score == 1.0  # 通用 15-25 optimal

    def test_extreme_air_temp_correction(self) -> None:
        """极端气温修正: air > 35°C → score - 0.1."""
        r = temperature.score_temperature(20.0, air_temp_c=40.0)
        assert abs(r.score - 0.9) < 0.01  # 1.0 - 0.1


# ---------------------------------------------------------------------------
# solunar.py 测试
# ---------------------------------------------------------------------------


class TestSolunar:
    """engine/solunar.py — 月相评分."""

    @patch("engine.solunar.get_moon_phase")
    def test_new_moon(self, mock_phase: MagicMock) -> None:
        """新月 (phase=0) → 1.0."""
        from unittest.mock import MagicMock

        mock_phase.return_value = (0.0, "新月", 0.0)
        r = solunar.score_solunar()
        assert r.score == 1.0

    @patch("engine.solunar.get_moon_phase")
    def test_full_moon(self, mock_phase: MagicMock) -> None:
        """满月 (phase=14.77) → 1.0."""
        from unittest.mock import MagicMock

        mock_phase.return_value = (14.77, "满月", 1.0)
        r = solunar.score_solunar()
        assert r.score == 1.0

    @patch("engine.solunar.get_moon_phase")
    def test_near_full_moon(self, mock_phase: MagicMock) -> None:
        """满月前后 1.5 天 → 0.85."""
        from unittest.mock import MagicMock

        mock_phase.return_value = (14.77 - 1.5, "盈凸月", 0.9)
        r = solunar.score_solunar()
        assert r.score == 0.85

    @patch("engine.solunar.get_moon_phase")
    def test_first_quarter(self, mock_phase: MagicMock) -> None:
        """上弦月 (phase~7.4) → 0.7."""
        from unittest.mock import MagicMock

        mock_phase.return_value = (7.38, "上弦月", 0.5)
        r = solunar.score_solunar()
        assert r.score == 0.7

    @patch("engine.solunar.get_moon_phase")
    def test_other_phase(self, mock_phase: MagicMock) -> None:
        """其他相位 → 0.5."""
        from unittest.mock import MagicMock

        mock_phase.return_value = (10.0, "盈凸月", 0.7)
        r = solunar.score_solunar()
        assert r.score == 0.5


# ---------------------------------------------------------------------------
# wind.py 测试
# ---------------------------------------------------------------------------


class TestWind:
    """engine/wind.py — 风况评分."""

    def test_calm(self) -> None:
        """无风 <1m/s → 0.5."""
        r = wind.score_wind(0.5)
        assert r.score == 0.5
        assert r.level == "calm"

    def test_optimal(self) -> None:
        """微风 1-3m/s → 1.0."""
        r = wind.score_wind(2.0)
        assert r.score == 1.0
        assert r.level == "optimal"

    def test_moderate(self) -> None:
        """中风 3-6m/s → 0.7."""
        r = wind.score_wind(4.5)
        assert r.score == 0.7
        assert r.level == "moderate"

    def test_strong(self) -> None:
        """大风 6-8m/s → 0.4."""
        r = wind.score_wind(7.0)
        assert r.score == 0.4
        assert r.level == "strong"

    def test_dangerous(self) -> None:
        """狂风 8-10m/s → 0.2."""
        r = wind.score_wind(9.0)
        assert r.score == 0.2
        assert r.level == "dangerous"

    def test_gale(self) -> None:
        """暴风 >10m/s → 0.1."""
        r = wind.score_wind(15.0)
        assert r.score == 0.1
        assert r.level == "dangerous"

    def test_with_direction(self) -> None:
        """有风向参数也能正常评分."""
        r = wind.score_wind(2.0, wind_direction_deg=45.0)
        assert r.score == 1.0
        assert r.wind_direction_deg == 45.0


# ---------------------------------------------------------------------------
# precipitation.py 测试
# ---------------------------------------------------------------------------


class TestPrecipitation:
    """engine/precipitation.py — 降水评分."""

    def test_none(self) -> None:
        """无雨 <0.1mm → 0.7."""
        r = precipitation.score_precipitation(0.0)
        assert r.score == 0.7
        assert r.level == "none"

    def test_light(self) -> None:
        """小雨 0.1-2.5mm → 0.9."""
        r = precipitation.score_precipitation(1.0)
        assert r.score == 0.9
        assert r.level == "light"

    def test_moderate(self) -> None:
        """中雨 2.5-8mm → 0.6."""
        r = precipitation.score_precipitation(5.0)
        assert r.score == 0.6
        assert r.level == "moderate"

    def test_heavy(self) -> None:
        """大雨 8-16mm → 0.3."""
        r = precipitation.score_precipitation(12.0)
        assert r.score == 0.3
        assert r.level == "heavy"

    def test_storm(self) -> None:
        """暴雨 16-25mm → 0.1."""
        r = precipitation.score_precipitation(20.0)
        assert r.score == 0.1
        assert r.level == "storm"

    def test_extreme_storm(self) -> None:
        """大暴雨 >25mm → 0.05."""
        r = precipitation.score_precipitation(30.0)
        assert r.score == 0.05
        assert r.level == "storm"

    def test_post_rain_window(self) -> None:
        """雨后转晴: 前日大雨+今天无雨 → 1.0."""
        r = precipitation.score_precipitation(0.0, prev_precip_mm=12.0)
        assert r.score == 1.0
        assert r.level == "post_rain"

    def test_no_post_rain_small_prev(self) -> None:
        """前日小雨+今天无雨 → 0.7（不是雨后窗口）."""
        r = precipitation.score_precipitation(0.0, prev_precip_mm=3.0)
        assert r.score == 0.7
        assert r.level == "none"


# ---------------------------------------------------------------------------
# weights.py 测试
# ---------------------------------------------------------------------------


class TestWeights:
    """engine/weights.py — 动态权重."""

    def test_default_weights(self) -> None:
        """默认权重总和 0.95."""
        w = weights.get_weights("spring")
        total = w.pressure + w.temperature + w.solunar + w.wind + w.precipitation + w.season + w.water
        assert abs(total - w.total) < 0.01
        assert 0.9 <= w.total <= 1.0  # 调整后仍接近 0.95

    def test_spring_adjustments(self) -> None:
        """春季: temperature+0.05, solunar+0.03."""
        w = weights.get_weights("spring")
        assert w.season_name == "spring"
        assert "temperature" in w.adjustments
        assert abs(w.adjustments["temperature"] - 0.05) < 0.01

    def test_summer_adjustments(self) -> None:
        """夏季: pressure+0.05, water+0.03."""
        w = weights.get_weights("summer")
        assert w.season_name == "summer"
        assert "pressure" in w.adjustments

    def test_winter_adjustments(self) -> None:
        """冬季: temperature+0.10."""
        w = weights.get_weights("winter")
        assert w.season_name == "winter"
        assert abs(w.adjustments["temperature"] - 0.10) < 0.01

    def test_feedback_max(self) -> None:
        """feedback 上限 0.10."""
        w = weights.get_weights("spring")
        assert w.feedback_max == 0.10

    def test_compute_score_all_perfect(self) -> None:
        """全 1.0 → 高分."""
        w = weights.get_weights("spring")
        score = weights.compute_fishing_score(
            pressure_score=1.0,
            temperature_score=1.0,
            solunar_score=1.0,
            wind_score=1.0,
            precipitation_score=1.0,
            season_score=1.0,
            water_score=1.0,
            weights=w,
        )
        assert score > 0.85

    def test_compute_score_all_zero(self) -> None:
        """全 0 → 低分."""
        w = weights.get_weights("summer")
        score = weights.compute_fishing_score(
            pressure_score=0.0,
            temperature_score=0.0,
            solunar_score=0.0,
            wind_score=0.0,
            precipitation_score=0.0,
            season_score=0.0,
            water_score=0.0,
            weights=w,
        )
        assert score < 0.15

    def test_feedback_positive(self) -> None:
        """正反馈 +0.10 提升分数."""
        w = weights.get_weights("spring")
        base = weights.compute_fishing_score(
            0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.0, w
        )
        boosted = weights.compute_fishing_score(
            0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.10, w
        )
        assert boosted >= base

    def test_feedback_clamped(self) -> None:
        """feedback 超出范围被钳制."""
        w = weights.get_weights("summer")
        # 给 1.0 的 feedback，应被钳制到 0.10
        r1 = weights.compute_fishing_score(
            0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.10, w
        )
        r2 = weights.compute_fishing_score(
            0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 1.0, w  # 超大
        )
        assert abs(r1 - r2) < 0.02  # 应该相近

    def test_score_range_0_1(self) -> None:
        """评分结果在 0-1 范围."""
        w = weights.get_weights("autumn")
        for p in [0.0, 0.3, 0.5, 0.8, 1.0]:
            score = weights.compute_fishing_score(
                p, p, p, p, p, p, p, 0.0, w
            )
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# water_temp.py 测试（需 mock weather API）
# ---------------------------------------------------------------------------


class TestWaterTemp:
    """engine/water_temp.py — 水温估算."""

    @patch("engine.water_temp.get_historical_avg_temp")
    def test_reservoir(self, mock_hist: MagicMock) -> None:
        """水库: air*0.75 + 3.5 - 2.0."""
        mock_hist.return_value = 20.0
        r = water_temp.estimate_water_temp(40.50, 117.00, water_type="reservoir")
        expected = 20.0 * 0.75 + 3.5 - 2.0  # 15 + 3.5 - 2 = 16.5
        assert abs(r.water_temp_c - round(expected, 1)) < 0.1
        assert r.water_type == "reservoir"
        assert r.data_quality == "full"

    @patch("engine.water_temp.get_historical_avg_temp")
    def test_river(self, mock_hist: MagicMock) -> None:
        """河流: air*0.75 + 3.5 + 0.0."""
        mock_hist.return_value = 20.0
        r = water_temp.estimate_water_temp(40.15, 116.65, water_type="river")
        expected = 20.0 * 0.75 + 3.5  # 18.5
        assert abs(r.water_temp_c - round(expected, 1)) < 0.1
        assert r.water_type == "river"

    @patch("engine.water_temp.get_historical_avg_temp")
    def test_black_pit(self, mock_hist: MagicMock) -> None:
        """黑坑: air*0.75 + 3.5 + 1.0."""
        mock_hist.return_value = 20.0
        r = water_temp.estimate_water_temp(39.92, 116.45, water_type="black_pit")
        expected = 20.0 * 0.75 + 3.5 + 1.0  # 19.5
        assert abs(r.water_temp_c - round(expected, 1)) < 0.1
        assert r.water_type == "black_pit"

    @patch("engine.water_temp.get_historical_avg_temp")
    def test_unknown_water_type_defaults_river(self, mock_hist: MagicMock) -> None:
        """未知 water_type → 默认 river."""
        mock_hist.return_value = 20.0
        r = water_temp.estimate_water_temp(40.0, 116.0, water_type="lake")
        assert r.water_type == "river"
        assert r.adjustment == 0.0

    @patch("engine.water_temp.get_historical_avg_temp")
    def test_degraded_no_historical_no_current(self, mock_hist: MagicMock) -> None:
        """降级: historical 和 current 都失败 → 季节默认值."""
        mock_hist.return_value = None
        with patch("services.weather.get_weather", return_value=None):
            r = water_temp.estimate_water_temp(40.0, 116.0, water_type="river")
            assert r.data_quality == "degraded"
            assert r.air_temp_3d_avg is None


# ---------------------------------------------------------------------------
# 全管道集成测试
# ---------------------------------------------------------------------------


class TestEngineIntegration:
    """全评分管道集成测试."""

    def test_all_modules_importable(self) -> None:
        """所有 engine 模块可 import."""
        assert hasattr(season, "get_season")
        assert hasattr(water_temp, "estimate_water_temp")
        assert hasattr(oxygen, "estimate_oxygen")
        assert hasattr(pressure, "score_pressure")
        assert hasattr(temperature, "score_temperature")
        assert hasattr(solunar, "score_solunar")
        assert hasattr(wind, "score_wind")
        assert hasattr(precipitation, "score_precipitation")
        assert hasattr(weights, "compute_fishing_score")

    def test_full_pipeline_mocked(self) -> None:
        """全管道 mock 测试（不依赖外部 API）."""
        with patch("engine.solunar.get_moon_phase") as mock_moon:
            mock_moon.return_value = (0.0, "新月", 0.0)

            si = season.get_season(date(2026, 7, 15))
            o2 = oxygen.estimate_oxygen(22.0)
            pr = pressure.score_pressure(1013.0, prev_hpa=1013.0)
            te = temperature.score_temperature(22.0, species_id="crucian_carp")
            so = solunar.score_solunar()
            wi = wind.score_wind(2.0)
            pp = precipitation.score_precipitation(0.5)
            w = weights.get_weights(si.season)

            score = weights.compute_fishing_score(
                pressure_score=pr.score,
                temperature_score=te.score,
                solunar_score=so.score,
                wind_score=wi.score,
                precipitation_score=pp.score,
                season_score=si.scoring_coefficient,
                water_score=o2.score,
                feedback_adjustment=0.0,
                weights=w,
            )

            assert 0.0 <= score <= 1.0
            assert si.season == "summer"
            assert o2.level == "excellent"
            assert pr.trend == "stable"
            assert te.level == "optimal"
            assert so.score == 1.0  # 新月
            assert wi.level == "optimal"
            assert pp.level == "light"
