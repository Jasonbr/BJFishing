"""tests/test_compliance.py — compliance/gate.py 合规测试.

T2.6: 边界 case + 黑坑豁免 + 饮用水源.

覆盖:
  - 禁渔期边界: 3.1/4.1/5.31/6.1/6.30/7.31/8.1/12.31/1.1/2.29(闰年)
  - 黑坑豁免: closed season 内 black_pit 不拦截
  - 饮用水源: miyun/huairou 全年禁钓
  - 非饮用水源: shisanling 不拦截
  - 禁用渔具: 电鱼/毒鱼/刺网 → blocked
  - 允许渔具: 单钩手竿 → not blocked
  - ComplianceResult 字段完整性 + to_dict()
"""

from __future__ import annotations

from datetime import date

import pytest

from compliance.gate import ComplianceResult, check_compliance


# ---------------------------------------------------------------------------
# 禁渔期边界 case（天然水域 river）
# ---------------------------------------------------------------------------

class TestClosedSeasonBoundary:
    """禁渔期边界 case：04-01 ~ 07-31，river 适用，black_pit 豁免."""

    @pytest.mark.parametrize(
        "test_date, expected_blocked, expected_closed",
        [
            (date(2026, 3, 1), False, False),    # 禁渔期前（春季首日）
            (date(2026, 4, 1), True, True),      # 禁渔期首日
            (date(2026, 5, 31), True, True),     # 禁渔期内
            (date(2026, 6, 1), True, True),      # 禁渔期内（夏季首日）
            (date(2026, 6, 30), True, True),     # 禁渔期内
            (date(2026, 7, 31), True, True),     # 禁渔期末日
            (date(2026, 8, 1), False, False),    # 禁渔期后
            (date(2026, 12, 31), False, False),  # 冬季
            (date(2026, 1, 1), False, False),    # 冬季跨年
            (date(2024, 2, 29), False, False),   # 闰年 2.29
        ],
    )
    def test_closed_season_river(
        self,
        test_date: date,
        expected_blocked: bool,
        expected_closed: bool,
    ) -> None:
        """river 在禁渔期内 blocked，禁渔期外 not blocked."""
        result = check_compliance(
            water_id="yongding_river",
            water_type="river",
            fishing_date=test_date,
        )
        assert result.block_analysis is expected_blocked
        assert result.closed_season_active is expected_closed

    def test_closed_season_start_boundary(self) -> None:
        """4.1 是禁渔期首日 — blocked."""
        result = check_compliance(
            water_type="river",
            fishing_date=date(2026, 4, 1),
        )
        assert result.block_analysis is True
        assert result.closed_season_active is True

    def test_closed_season_end_boundary(self) -> None:
        """7.31 是禁渔期末日 — blocked，8.1 not blocked."""
        blocked = check_compliance(
            water_type="river",
            fishing_date=date(2026, 7, 31),
        )
        assert blocked.block_analysis is True

        not_blocked = check_compliance(
            water_type="river",
            fishing_date=date(2026, 8, 1),
        )
        assert not_blocked.block_analysis is False

    def test_closed_season_reservoir(self) -> None:
        """reservoir 也适用禁渔期."""
        result = check_compliance(
            water_id="shisanling_reservoir",
            water_type="reservoir",
            fishing_date=date(2026, 5, 15),
        )
        assert result.block_analysis is True
        assert result.closed_season_active is True


# ---------------------------------------------------------------------------
# 黑坑豁免
# ---------------------------------------------------------------------------

class TestBlackPitExemption:
    """黑坑豁免禁渔期 — closed season 内不拦截."""

    def test_black_pit_during_closed_season(self) -> None:
        """黑坑在禁渔期内不拦截."""
        result = check_compliance(
            water_id="black_pit",
            water_type="black_pit",
            fishing_date=date(2026, 5, 15),  # 禁渔期内
        )
        assert result.block_analysis is False
        assert result.closed_season_active is False

    def test_black_pit_summer(self) -> None:
        """黑坑夏季不拦截."""
        result = check_compliance(
            water_type="black_pit",
            fishing_date=date(2026, 7, 1),
        )
        assert result.block_analysis is False
        assert result.closed_season_active is False

    def test_black_pit_winter(self) -> None:
        """黑坑冬季不拦截."""
        result = check_compliance(
            water_type="black_pit",
            fishing_date=date(2026, 12, 15),
        )
        assert result.block_analysis is False


# ---------------------------------------------------------------------------
# 饮用水源保护区（全年禁钓）
# ---------------------------------------------------------------------------

class TestDrinkingWaterSource:
    """饮用水源保护区全年禁钓 — 不受禁渔期限制."""

    def test_miyun_reservoir_summer(self) -> None:
        """密云水库夏季（禁渔期外）也 blocked."""
        result = check_compliance(
            water_id="miyun_reservoir",
            water_type="reservoir",
            fishing_date=date(2026, 9, 15),  # 禁渔期外
        )
        assert result.block_analysis is True
        assert result.closed_season_active is False

    def test_miyun_reservoir_closed_season(self) -> None:
        """密云水库禁渔期内也 blocked（双重理由）."""
        result = check_compliance(
            water_id="miyun_reservoir",
            water_type="reservoir",
            fishing_date=date(2026, 5, 15),  # 禁渔期内
        )
        assert result.block_analysis is True

    def test_huairou_reservoir_winter(self) -> None:
        """怀柔水库冬季也 blocked."""
        result = check_compliance(
            water_id="huairou_reservoir",
            water_type="reservoir",
            fishing_date=date(2026, 12, 15),
        )
        assert result.block_analysis is True

    def test_shisanling_not_drinking_water(self) -> None:
        """十三陵水库非饮用水源 — 禁渔期外不拦截."""
        result = check_compliance(
            water_id="shisanling_reservoir",
            water_type="reservoir",
            fishing_date=date(2026, 9, 15),  # 禁渔期外
        )
        assert result.block_analysis is False


# ---------------------------------------------------------------------------
# 禁用渔具
# ---------------------------------------------------------------------------

class TestBannedGear:
    """禁用渔具渔法检查."""

    @pytest.mark.parametrize(
        "gear_id",
        [
            "electric_fishing",
            "poison_fishing",
            "explosive_fishing",
            "gill_net",
            "fish_trap",
            "multi_hook_longline",
        ],
    )
    def test_banned_gear_blocks(self, gear_id: str) -> None:
        """所有禁用渔具都应 blocked."""
        result = check_compliance(
            water_type="black_pit",
            fishing_date=date(2026, 9, 15),
            gear_id=gear_id,
        )
        assert result.block_analysis is True
        assert any("禁用渔具" in r for r in result.reasons)

    def test_allowed_gear_not_blocked(self) -> None:
        """允许的渔具不拦截."""
        result = check_compliance(
            water_type="black_pit",
            fishing_date=date(2026, 9, 15),
            gear_id="single_hook_rod",
        )
        assert result.block_analysis is False

    def test_no_gear_not_blocked(self) -> None:
        """不指定渔具不拦截."""
        result = check_compliance(
            water_type="black_pit",
            fishing_date=date(2026, 9, 15),
            gear_id=None,
        )
        assert result.block_analysis is False


# ---------------------------------------------------------------------------
# ComplianceResult 字段完整性
# ---------------------------------------------------------------------------

class TestComplianceResultFields:
    """ComplianceResult 返回值字段完整性."""

    def test_result_has_version(self) -> None:
        """结果包含版本号."""
        result = check_compliance(
            water_type="black_pit",
            fishing_date=date(2026, 9, 15),
        )
        assert result.version != ""
        assert result.version != "unknown"

    def test_result_has_effective_date(self) -> None:
        """结果包含生效日期."""
        result = check_compliance(
            water_type="black_pit",
            fishing_date=date(2026, 9, 15),
        )
        assert result.effective_date != ""
        assert result.effective_date != "unknown"

    def test_result_has_compliance_notes(self) -> None:
        """结果包含 5 条合规提醒."""
        result = check_compliance(
            water_type="black_pit",
            fishing_date=date(2026, 9, 15),
        )
        assert len(result.compliance_notes) >= 5

    def test_blocked_result_has_reasons(self) -> None:
        """blocked 结果有 reasons."""
        result = check_compliance(
            water_type="river",
            fishing_date=date(2026, 5, 15),
        )
        assert result.block_analysis is True
        assert len(result.reasons) > 0

    def test_not_blocked_result_empty_reasons(self) -> None:
        """not blocked 结果 reasons 为空."""
        result = check_compliance(
            water_type="black_pit",
            fishing_date=date(2026, 9, 15),
        )
        assert result.block_analysis is False
        assert len(result.reasons) == 0

    def test_water_type_resolved_from_explicit(self) -> None:
        """显式 water_type 优先."""
        result = check_compliance(
            water_id="yongding_river",
            water_type="river",
            fishing_date=date(2026, 9, 15),
        )
        assert result.water_type == "river"

    def test_water_type_resolved_from_yaml(self) -> None:
        """不传 water_type 时从 yaml 查询."""
        result = check_compliance(
            water_id="yongding_river",
            fishing_date=date(2026, 9, 15),
        )
        assert result.water_type == "river"

    def test_water_id_in_result(self) -> None:
        """结果包含 water_id."""
        result = check_compliance(
            water_id="miyun_reservoir",
            water_type="reservoir",
            fishing_date=date(2026, 9, 15),
        )
        assert result.water_id == "miyun_reservoir"


# ---------------------------------------------------------------------------
# to_dict() 序列化
# ---------------------------------------------------------------------------

class TestComplianceResultToDict:
    """ComplianceResult.to_dict() 序列化测试."""

    def test_to_dict_keys(self) -> None:
        """to_dict 包含所有必要字段."""
        result = check_compliance(
            water_type="river",
            fishing_date=date(2026, 5, 15),
        )
        d = result.to_dict()
        assert "block_analysis" in d
        assert "reasons" in d
        assert "compliance_notes" in d
        assert "effective_date" in d
        assert "version" in d
        assert "closed_season_active" in d
        assert "water_type" in d
        assert "water_id" in d

    def test_to_dict_block_analysis_matches(self) -> None:
        """to_dict 的 block_analysis 与 result 一致."""
        result = check_compliance(
            water_type="river",
            fishing_date=date(2026, 5, 15),
        )
        d = result.to_dict()
        assert d["block_analysis"] == result.block_analysis

    def test_to_dict_reasons_match(self) -> None:
        """to_dict 的 reasons 与 result 一致."""
        result = check_compliance(
            water_type="river",
            fishing_date=date(2026, 5, 15),
        )
        d = result.to_dict()
        assert d["reasons"] == result.reasons


# ---------------------------------------------------------------------------
# 默认日期 (None → 当前日期)
# ---------------------------------------------------------------------------

class TestDefaultDate:
    """fishing_date=None 时使用当前 BJ_TZ 日期."""

    def test_none_date_does_not_crash(self) -> None:
        """fishing_date=None 不崩溃."""
        result = check_compliance(
            water_type="black_pit",
            fishing_date=None,
        )
        assert result is not None
        assert isinstance(result, ComplianceResult)

    def test_datetime_input_accepted(self) -> None:
        """datetime 输入也能处理."""
        from datetime import datetime

        result = check_compliance(
            water_type="river",
            fishing_date=datetime(2026, 5, 15, 10, 30),
        )
        assert result.block_analysis is True
