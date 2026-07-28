"""tests/test_integration.py — T3.5 集成测试.

极端天气场景 + 全流程管道测试:
  - 暴雨 (precipitation > 10mm)
  - 大风 (wind_speed > 10 m/s)
  - 极寒 (temperature < -10C)
  - 高温 (temperature > 35C)
  - 降级模式 (weather=None)
  - 合规拦截 (closed season)
  - JSON 报告模式
  - LLM fallback (both fail -> JSON)
  - 策略模块覆盖
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest

from compliance.gate import ComplianceResult
from config import BJ_TZ
from services.astronomy import AstronomyInfo
from strategy.bait import recommend_bait
from strategy.position import recommend_position
from strategy.risk import assess_risk
from strategy.time_windows import recommend_time_windows
from tools.analyze import analyze_fishing
from tools.report import report_fishing


# ==================== Mock 数据构造 ====================


def _make_weather(
    temp: float = 25.0,
    precip: float = 0.0,
    wind_speed: float = 3.0,
    wind_dir: float = 180.0,
    pressure: float = 1013.0,
    humidity: float = 60.0,
    daily_precip: list[float] | None = None,
) -> dict[str, Any]:
    """构造 Open-Meteo 天气响应."""
    return {
        "current": {
            "temperature_2m": temp,
            "relative_humidity_2m": humidity,
            "precipitation": precip,
            "wind_speed_10m": wind_speed,
            "wind_direction_10m": wind_dir,
            "surface_pressure": pressure,
        },
        "daily": {
            "temperature_2m_max": [temp + 3],
            "temperature_2m_min": [temp - 3],
            "precipitation_sum": daily_precip or [precip, 0.0],
            "wind_speed_10m_max": [wind_speed],
            "sunrise": ["2026-07-28T05:00"],
            "sunset": ["2026-07-28T19:30"],
            "uv_index_max": [8.0],
        },
    }


def _make_astronomy() -> AstronomyInfo:
    """构造天文信息."""
    return AstronomyInfo(
        moon_phase=10.0,
        moon_phase_name="上弦月",
        moon_illumination=0.5,
        sunrise=datetime(2026, 7, 28, 5, 0, tzinfo=BJ_TZ),
        sunset=datetime(2026, 7, 28, 19, 30, tzinfo=BJ_TZ),
        solar_noon=datetime(2026, 7, 28, 12, 15, tzinfo=BJ_TZ),
        dawn=datetime(2026, 7, 28, 4, 30, tzinfo=BJ_TZ),
        dusk=datetime(2026, 7, 28, 20, 0, tzinfo=BJ_TZ),
        golden_hour_morning=(
            datetime(2026, 7, 28, 5, 0, tzinfo=BJ_TZ),
            datetime(2026, 7, 28, 6, 0, tzinfo=BJ_TZ),
        ),
        golden_hour_evening=(
            datetime(2026, 7, 28, 18, 30, tzinfo=BJ_TZ),
            datetime(2026, 7, 28, 19, 30, tzinfo=BJ_TZ),
        ),
        moonrise=datetime(2026, 7, 28, 14, 0, tzinfo=BJ_TZ),
        moonset=datetime(2026, 7, 28, 2, 0, tzinfo=BJ_TZ),
    )


def _mock_services(
    weather: dict[str, Any] | None = None,
    hist_temp: float = 20.0,
    astro: AstronomyInfo | None = None,
) -> ExitStack:
    """批量 mock 所有 service 层调用 + 合规放行."""
    stack = ExitStack()
    if weather is None:
        weather = _make_weather()
    if astro is None:
        astro = _make_astronomy()

    allowed = ComplianceResult(
        block_analysis=False,
        reasons=[],
        compliance_notes=[],
        effective_date="2026-01-01",
        version="2026.1",
        closed_season_active=False,
        water_type="river",
        water_id="test_river",
    )

    mocks: dict[str, Any] = {
        "tools.collect.get_location": (39.9, 116.4),
        "tools.collect.get_weather": weather,
        "tools.collect.get_historical_avg_temp": hist_temp,
        "tools.collect.get_astronomy": astro,
        "engine.water_temp.get_historical_avg_temp": hist_temp,
        "tools.analyze.check_compliance": allowed,
    }
    for target, ret_val in mocks.items():
        stack.enter_context(patch(target, return_value=ret_val))
    return stack


def _mock_compliance_blocked() -> ExitStack:
    """mock 合规拦截."""
    stack = ExitStack()
    blocked = ComplianceResult(
        block_analysis=True,
        reasons=["禁渔期（04-01~07-31）天然水域禁止垂钓"],
        compliance_notes=["请8月1日后出钓"],
        effective_date="2026-01-01",
        version="2026.1",
        closed_season_active=True,
        water_type="river",
        water_id="test_river",
    )
    stack.enter_context(
        patch("tools.analyze.check_compliance", return_value=blocked),
    )
    return stack


# ==================== 极端天气场景 ====================


class TestExtremeWeather:
    """T3.5: 极端天气场景测试."""

    @pytest.mark.parametrize(
        "scenario,weather_kwargs,expected_risk",
        [
            ("heavy_rain", {"precip": 15.0}, "caution"),
            ("strong_wind", {"wind_speed": 12.0}, "dangerous"),
            ("extreme_cold", {"temp": -15.0}, "caution"),
            ("extreme_heat", {"temp": 38.0}, "caution"),
            ("normal", {"temp": 25.0, "wind_speed": 3.0}, "safe"),
        ],
        ids=["heavy_rain", "strong_wind", "extreme_cold", "extreme_heat", "normal"],
    )
    def test_extreme_weather_risk(
        self, scenario: str, weather_kwargs: dict[str, Any], expected_risk: str,
    ) -> None:
        """极端天气 -> 风险等级正确."""
        weather = _make_weather(**weather_kwargs)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="测试点", water_type="river"),
            )
        assert result["fishing_score"] is not None
        assert result["data_quality"] in ("full", "partial")
        risk = assess_risk(result)
        assert risk["level"] == expected_risk

    def test_heavy_rain_precip_score(self) -> None:
        """暴雨 -> 降水评分偏低."""
        weather = _make_weather(precip=15.0)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="暴雨测试", water_type="river"),
            )
        precip_score = result["sub_scores"]["precipitation"]
        assert precip_score < 0.5

    def test_strong_wind_score(self) -> None:
        """大风 -> 风评分偏低."""
        weather = _make_weather(wind_speed=12.0)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="大风测试", water_type="river"),
            )
        wind_score = result["sub_scores"]["wind"]
        assert wind_score < 0.5

    def test_extreme_cold_temp_score(self) -> None:
        """极寒 -> 温度评分偏低."""
        weather = _make_weather(temp=-15.0)
        with _mock_services(weather=weather, hist_temp=-12.0):
            result = asyncio.run(
                analyze_fishing(spot_name="极寒测试", water_type="river"),
            )
        temp_score = result["sub_scores"]["temperature"]
        assert temp_score < 0.7  # 38C is suboptimal but not terrible

    def test_extreme_heat_temp_score(self) -> None:
        """高温 -> 温度评分偏低."""
        weather = _make_weather(temp=38.0)
        with _mock_services(weather=weather, hist_temp=35.0):
            result = asyncio.run(
                analyze_fishing(spot_name="高温测试", water_type="river"),
            )
        temp_score = result["sub_scores"]["temperature"]
        assert temp_score < 0.7  # 38C is suboptimal but not terrible


# ==================== 全流程管道测试 ====================


class TestFullPipeline:
    """全流程: collect -> analyze -> report."""

    def test_normal_pipeline(self) -> None:
        """正常流程: 采集->分析->评分->策略."""
        with _mock_services():
            result = asyncio.run(
                analyze_fishing(spot_name="昆明湖", water_type="river"),
            )
        assert result["fishing_score"] is not None
        assert 0.0 <= result["fishing_score"] <= 1.0
        assert result["data_quality"] == "full"
        assert result["confidence"] == "high"
        for key in ("pressure", "temperature", "solunar", "wind",
                     "precipitation", "season", "water"):
            assert key in result["sub_scores"]
        assert result["compliance"]["block_analysis"] is False

    def test_degraded_mode_weather_none(self) -> None:
        """天气获取失败 -> 降级模式."""
        with patch("tools.collect.get_weather", return_value=None), \
             patch("tools.collect.get_location", return_value=(39.9, 116.4)):
            result = asyncio.run(
                analyze_fishing(spot_name="降级测试", water_type="river"),
            )
        assert result["fishing_score"] is None
        assert result["data_quality"] == "degraded"
        assert result["confidence"] == "none"
        assert result["sub_scores"] == {}

    def test_compliance_block(self) -> None:
        """合规拦截 -> blocked 响应."""
        with _mock_services(), _mock_compliance_blocked():
            result = asyncio.run(
                analyze_fishing(spot_name="禁渔期测试", water_type="river"),
            )
        assert result["compliance"]["block_analysis"] is True
        assert len(result["compliance"]["reasons"]) > 0

    def test_json_report_normal(self) -> None:
        """JSON 报告模式 -> 结构化输出."""
        with _mock_services():
            analysis = asyncio.run(
                analyze_fishing(spot_name="报告测试", water_type="river"),
            )
            report = asyncio.run(
                report_fishing(analysis_result=analysis, output_mode="json"),
            )
        assert report["fishing_score"] is not None
        for key in ("position", "bait", "time_windows", "risk"):
            assert key in report["strategy"]
        assert report["disclaimer"] != ""
        assert report["llm_report"] is None

    def test_json_report_blocked(self) -> None:
        """合规拦截 -> blocked 报告 schema."""
        with _mock_services(), _mock_compliance_blocked():
            analysis = asyncio.run(
                analyze_fishing(spot_name="blocked", water_type="river"),
            )
            report = asyncio.run(
                report_fishing(analysis_result=analysis, output_mode="json"),
            )
        assert report["status"] == "blocked"
        assert "reason" in report
        assert "alternative_suggestions" in report
        assert len(report["alternative_suggestions"]) > 0
        assert report["disclaimer"] != ""

    def test_report_with_lat_lng(self) -> None:
        """直接传坐标（无 spot_name）."""
        with _mock_services():
            result = asyncio.run(
                analyze_fishing(lat=39.9, lng=116.4, water_type="river"),
            )
        assert result["fishing_score"] is not None
        assert result["lat"] == 39.9
        assert result["lng"] == 116.4

    def test_partial_data_quality(self) -> None:
        """部分数据缺失 -> partial."""
        weather = _make_weather()
        with _mock_services(weather=weather):
            with patch("tools.collect.get_historical_avg_temp", return_value=None):
                result = asyncio.run(
                    analyze_fishing(spot_name="partial", water_type="river"),
                )
        assert result["data_quality"] in ("full", "partial")

    def test_weights_in_output(self) -> None:
        """分析结果包含权重信息."""
        with _mock_services():
            result = asyncio.run(
                analyze_fishing(spot_name="权重测试", water_type="river"),
            )
        assert "weights_used" in result
        for key in ("pressure", "temperature", "water"):
            assert key in result["weights_used"]

    def test_sub_results_serialized(self) -> None:
        """sub_results 被序列化为 dict."""
        with _mock_services():
            result = asyncio.run(
                analyze_fishing(spot_name="序列化", water_type="river"),
            )
        sub_results = result["sub_results"]
        assert isinstance(sub_results, dict)
        assert "pressure" in sub_results
        assert isinstance(sub_results["pressure"], dict)
        assert "score" in sub_results["pressure"]


# ==================== LLM Fallback 测试 ====================


class TestLLMFallback:
    """多 LLM 自动切换: Qwen 失败->Ollama->JSON fallback."""

    def test_qwen_success(self) -> None:
        """Qwen API 成功."""
        with _mock_services():
            analysis = asyncio.run(
                analyze_fishing(spot_name="qwen", water_type="river"),
            )
            with patch("tools.report._call_qwen", return_value="Qwen建议内容"):
                report = asyncio.run(
                    report_fishing(analysis_result=analysis, output_mode="qwen"),
                )
        assert report["llm_report"] == "Qwen建议内容"
        assert "position" in report["strategy"]

    def test_qwen_fail_ollama_fallback(self) -> None:
        """Qwen 失败 -> Ollama fallback."""
        with _mock_services():
            analysis = asyncio.run(
                analyze_fishing(spot_name="fallback", water_type="river"),
            )
            with patch("tools.report._call_qwen", return_value=None), \
                 patch("tools.report._call_ollama", return_value="Ollama建议内容"):
                report = asyncio.run(
                    report_fishing(analysis_result=analysis, output_mode="qwen"),
                )
        assert report["llm_report"] == "Ollama建议内容"

    def test_both_fail_json_fallback(self) -> None:
        """两个 LLM 都失败 -> JSON fallback."""
        with _mock_services():
            analysis = asyncio.run(
                analyze_fishing(spot_name="both_fail", water_type="river"),
            )
            with patch("tools.report._call_qwen", return_value=None), \
                 patch("tools.report._call_ollama", return_value=None):
                report = asyncio.run(
                    report_fishing(analysis_result=analysis, output_mode="qwen"),
                )
        assert report["llm_report"] is None
        assert report["fishing_score"] is not None

    def test_ollama_success(self) -> None:
        """Ollama 直接调用成功."""
        with _mock_services():
            analysis = asyncio.run(
                analyze_fishing(spot_name="ollama", water_type="river"),
            )
            with patch("tools.report._call_ollama", return_value="Ollama内容"):
                report = asyncio.run(
                    report_fishing(analysis_result=analysis, output_mode="ollama"),
                )
        assert report["llm_report"] == "Ollama内容"

    def test_ollama_fail_qwen_fallback(self) -> None:
        """Ollama 失败 -> Qwen fallback."""
        with _mock_services():
            analysis = asyncio.run(
                analyze_fishing(spot_name="o2q", water_type="river"),
            )
            with patch("tools.report._call_ollama", return_value=None), \
                 patch("tools.report._call_qwen", return_value="Qwen兜底内容"):
                report = asyncio.run(
                    report_fishing(analysis_result=analysis, output_mode="ollama"),
                )
        assert report["llm_report"] == "Qwen兜底内容"


# ==================== 策略模块测试 ====================


class TestPositionStrategy:
    """钓位策略测试."""

    def test_river_strong_wind(self) -> None:
        """河流 + 大风 -> 避风岸."""
        weather = _make_weather(wind_speed=6.0, wind_dir=90)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="河风", water_type="river"),
            )
        pos = recommend_position(result)
        assert "避风" in pos["recommendation"] or "下游" in pos["recommendation"]

    def test_reservoir_strong_wind(self) -> None:
        """水库 + 大风 -> 迎风岸."""
        weather = _make_weather(wind_speed=6.0, wind_dir=180)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="水库", water_type="reservoir"),
            )
        pos = recommend_position(result)
        assert "迎风" in pos["recommendation"]

    def test_no_wind_summer(self) -> None:
        """无风 + 夏季 -> 深水/树荫."""
        weather = _make_weather(wind_speed=0.5)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="夏无风", water_type="river"),
            )
        pos = recommend_position(result)
        assert pos["recommendation"] != ""

    def test_wind_direction_text(self) -> None:
        """风向文字转换正确."""
        weather = _make_weather(wind_speed=4.0, wind_dir=0)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="北风", water_type="river"),
            )
        pos = recommend_position(result)
        assert "北" in pos["wind_strategy"] or "风" in pos["wind_strategy"]


class TestBaitStrategy:
    """饵料策略测试."""

    def test_cold_water_animal_bait(self) -> None:
        """低温 -> 动物饵."""
        weather = _make_weather(temp=5.0)
        with _mock_services(weather=weather, hist_temp=5.0):
            result = asyncio.run(
                analyze_fishing(spot_name="冷饵", water_type="river"),
            )
        bait = recommend_bait(result)
        assert "红虫" in bait["primary"] or "蚯蚓" in bait["primary"]
        assert bait["water_temp_c"] < 10.0

    def test_warm_water_plant_bait(self) -> None:
        """高温 -> 植物饵."""
        weather = _make_weather(temp=28.0)
        with _mock_services(weather=weather, hist_temp=27.0):
            result = asyncio.run(
                analyze_fishing(spot_name="热饵", water_type="river"),
            )
        bait = recommend_bait(result)
        assert "玉米" in bait["primary"] or "面饵" in bait["primary"]
        assert bait["water_temp_c"] > 20.0

    def test_moderate_temp_mixed_bait(self) -> None:
        """适温 -> 荤素搭配."""
        weather = _make_weather(temp=18.0)
        with _mock_services(weather=weather, hist_temp=17.0):
            result = asyncio.run(
                analyze_fishing(spot_name="温饵", water_type="river"),
            )
        bait = recommend_bait(result)
        assert "蚯蚓" in bait["primary"] or "玉米" in bait["primary"]

    def test_bait_has_reason(self) -> None:
        """饵料推荐有原因说明."""
        with _mock_services():
            result = asyncio.run(
                analyze_fishing(spot_name="reason", water_type="river"),
            )
        bait = recommend_bait(result)
        assert bait["reason"] != ""
        assert "secondary" in bait
        assert len(bait["secondary"]) > 0


class TestTimeWindows:
    """时段策略测试."""

    def test_golden_hours_present(self) -> None:
        """黄金时段存在."""
        with _mock_services():
            result = asyncio.run(
                analyze_fishing(spot_name="黄金", water_type="river"),
            )
        tw = recommend_time_windows(result)
        assert len(tw["windows"]) > 0
        high_priority = [w for w in tw["windows"] if w["priority"] == "high"]
        assert len(high_priority) > 0

    def test_solunar_score_in_output(self) -> None:
        """月相评分在输出中."""
        with _mock_services():
            result = asyncio.run(
                analyze_fishing(spot_name="月相", water_type="river"),
            )
        tw = recommend_time_windows(result)
        assert "solunar_score" in tw

    def test_best_window_exists(self) -> None:
        """最佳时段存在."""
        with _mock_services():
            result = asyncio.run(
                analyze_fishing(spot_name="best", water_type="river"),
            )
        tw = recommend_time_windows(result)
        assert tw["best_window"] is not None
        assert "type" in tw["best_window"]
        assert "start" in tw["best_window"]


class TestRiskAssessment:
    """风险评估策略测试."""

    def test_safe_conditions(self) -> None:
        """正常天气 -> safe."""
        weather = _make_weather(temp=25.0, wind_speed=2.0, precip=0.0)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="safe", water_type="river"),
            )
        risk = assess_risk(result)
        assert risk["level"] == "safe"
        assert len(risk["warnings"]) == 0

    def test_dangerous_wind(self) -> None:
        """大风 -> dangerous."""
        weather = _make_weather(wind_speed=12.0)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="danger", water_type="river"),
            )
        risk = assess_risk(result)
        assert risk["level"] == "dangerous"
        assert len(risk["warnings"]) > 0

    def test_caution_heavy_rain(self) -> None:
        """暴雨 -> caution."""
        weather = _make_weather(precip=15.0)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="rain", water_type="river"),
            )
        risk = assess_risk(result)
        assert risk["level"] == "caution"
        assert len(risk["warnings"]) > 0

    def test_prohibited_compliance(self) -> None:
        """合规拦截 -> prohibited."""
        with _mock_services(), _mock_compliance_blocked():
            result = asyncio.run(
                analyze_fishing(spot_name="prohibited", water_type="river"),
            )
        risk = assess_risk(result)
        assert risk["level"] == "prohibited"
        assert len(risk["warnings"]) > 0

    def test_safety_tips_present(self) -> None:
        """安全提示存在."""
        with _mock_services():
            result = asyncio.run(
                analyze_fishing(spot_name="tips", water_type="river"),
            )
        risk = assess_risk(result)
        assert len(risk["safety_tips"]) > 0

class TestSeasonWaterMatrix:
    """T5.5: 3 水域 × 4 季节验收矩阵 (12 组合)."""

    @pytest.mark.parametrize("water_type", ["river", "reservoir", "black_pit"])
    @pytest.mark.parametrize("season,temp", [
        ("spring", 15.0),
        ("summer", 30.0),
        ("autumn", 15.0),
        ("winter", 0.0),
    ])
    def test_season_water_matrix(self, water_type: str, season: str, temp: float) -> None:
        """每种 水域×季节 组合都能产出有效评分."""
        weather = _make_weather(temp=temp, wind_speed=3.0)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name=f"test_{season}", water_type=water_type),
            )
        assert result["data_quality"] in ("full", "partial")
        assert "fishing_score" in result
        score = result["fishing_score"]
        assert score is not None
        assert 0.0 <= score <= 1.0
        assert "sub_scores" in result
        assert len(result["sub_scores"]) >= 5

    def test_black_pit_not_blocked_in_closed_season(self) -> None:
        """黑坑在禁渔期不受拦截（合规豁免）."""
        weather = _make_weather(temp=28.0)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="黑坑", water_type="black_pit"),
            )
        assert result["compliance"]["block_analysis"] is False
        assert result["fishing_score"] is not None

    def test_reservoir_summer_score_reasonable(self) -> None:
        """水库夏季评分应该 > 0.3（夏季条件较好）."""
        weather = _make_weather(temp=28.0, wind_speed=2.0)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="水库", water_type="reservoir"),
            )
        assert result["fishing_score"] is not None
        assert result["fishing_score"] > 0.3

    def test_river_winter_score_low(self) -> None:
        """河流冬季评分应该 < 0.6（冬季条件较差）."""
        weather = _make_weather(temp=0.0, wind_speed=5.0)
        with _mock_services(weather=weather):
            result = asyncio.run(
                analyze_fishing(spot_name="河流", water_type="river"),
            )
        assert result["fishing_score"] is not None
        assert result["fishing_score"] < 0.85

