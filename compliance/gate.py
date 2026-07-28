"""compliance/gate.py — 合规前置拦截.

T2.5: 禁渔期判断在此层（season.py 只判季节）.
加载 compliance_2026.yaml + beijing_waters.yaml, 综合判定 block_analysis.

合规检查维度（优先级从高到低）:
  1. 饮用水源保护区（全年禁钓）— miyun/huairou reservoir
  2. 天然水域禁渔期（04-01 ~ 07-31）— river/reservoir, 黑坑豁免
  3. 禁用渔具渔法 — electric/poison/explosive/gill_net/fish_trap/multi_hook_longline

被 tools/analyze.py 调用: compliance = check_compliance(...); if compliance.block_analysis: skip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from config import BJ_TZ, KNOWLEDGE_DIR
from engine.season import get_season

logger = logging.getLogger(__name__)

_COMPLIANCE_PATH: Path = KNOWLEDGE_DIR / "compliance_2026.yaml"
_WATERS_PATH: Path = KNOWLEDGE_DIR / "beijing_waters.yaml"

_compliance_cache: dict[str, Any] | None = None
_waters_cache: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True)
class ComplianceResult:
    """合规检查结果."""

    block_analysis: bool
    reasons: list[str]
    compliance_notes: list[str]
    effective_date: str
    version: str
    closed_season_active: bool
    water_type: str | None
    water_id: str | None

    def to_dict(self) -> dict[str, Any]:
        """转为 dict（嵌入 analyze 输出）."""
        return {
            "block_analysis": self.block_analysis,
            "reasons": self.reasons,
            "compliance_notes": self.compliance_notes,
            "effective_date": self.effective_date,
            "version": self.version,
            "closed_season_active": self.closed_season_active,
            "water_type": self.water_type,
            "water_id": self.water_id,
        }


def _load_compliance() -> dict[str, Any]:
    """加载 compliance_2026.yaml 并缓存."""
    global _compliance_cache
    if _compliance_cache is not None:
        return _compliance_cache
    try:
        with open(_COMPLIANCE_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning("compliance_2026.yaml not found, using permissive defaults")
        _compliance_cache = _default_compliance()
        return _compliance_cache
    _compliance_cache = data if data is not None else _default_compliance()
    return _compliance_cache


def _default_compliance() -> dict[str, Any]:
    """硬编码默认值（YAML 加载失败时 fallback — permissive）."""
    return {
        "meta": {
            "version": "fallback",
            "effective_date": "2026-01-01",
            "superseded_by": None,
        },
        "closed_season": {
            "natural_waters": {
                "start": "04-01",
                "end": "07-31",
                "reason": "鱼类繁殖期保护",
                "affected_water_types": ["river", "reservoir"],
                "exempt_water_types": ["black_pit"],
            }
        },
        "drinking_water_protection": {
            "year_round_banned": [],
        },
        "banned_gear": [],
        "compliance_reminders": [
            "鱼情预判仅供参考，请以实际天气和水情为准，注意人身安全",
        ],
    }


def _load_waters() -> dict[str, dict[str, Any]]:
    """加载 beijing_waters.yaml 并索引 water_id."""
    global _waters_cache
    if _waters_cache is not None:
        return _waters_cache
    try:
        with open(_WATERS_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning("beijing_waters.yaml not found")
        _waters_cache = {}
        return _waters_cache

    waters: dict[str, dict[str, Any]] = {}
    for w in data.get("waters", []):
        wid = w.get("id", "")
        waters[wid] = w
    bp = data.get("black_pit_category", {})
    if bp:
        waters["black_pit"] = bp
    _waters_cache = waters
    return _waters_cache


def _parse_mmdd(s: str) -> tuple[int, int]:
    """解析 'MM-DD' 字符串为 (month, day)."""
    parts = s.split("-")
    return int(parts[0]), int(parts[1])


def _is_in_closed_season(
    check_date: date,
    closed_season_cfg: dict[str, Any],
) -> bool:
    """判断日期是否在禁渔期内（04-01 ~ 07-31，不跨年）."""
    natural = closed_season_cfg.get("natural_waters", {})
    start_str = natural.get("start", "04-01")
    end_str = natural.get("end", "07-31")
    try:
        sm, sd = _parse_mmdd(start_str)
        em, ed = _parse_mmdd(end_str)
        start_ord = date(check_date.year, sm, sd).toordinal()
        end_ord = date(check_date.year, em, ed).toordinal()
    except (ValueError, KeyError):
        logger.error("Invalid closed season config: %s ~ %s", start_str, end_str)
        return False
    return start_ord <= check_date.toordinal() <= end_ord


def _is_drinking_water_source(
    water_id: str | None,
    waters: dict[str, dict[str, Any]],
) -> bool:
    """判断水域是否为饮用水源保护区."""
    if water_id is None:
        return False
    w = waters.get(water_id, {})
    return bool(w.get("is_drinking_water_source", False))


def _is_year_round_banned(
    water_id: str | None,
    waters: dict[str, dict[str, Any]],
) -> bool:
    """判断水域是否全年禁钓."""
    if water_id is None:
        return False
    w = waters.get(water_id, {})
    return bool(w.get("year_round_fishing_banned", False))


def _is_banned_gear(
    gear_id: str | None,
    compliance_cfg: dict[str, Any],
) -> bool:
    """判断渔具是否禁用."""
    if gear_id is None:
        return False
    for g in compliance_cfg.get("banned_gear", []):
        if g.get("id") == gear_id:
            return True
    return False


def _get_water_type(
    water_id: str | None,
    water_type: str | None,
    waters: dict[str, dict[str, Any]],
) -> str | None:
    """确定水域类型: 显式参数 > yaml 查询."""
    if water_type is not None:
        return water_type
    if water_id is not None:
        raw = waters.get(water_id, {}).get("type")
        if isinstance(raw, str):
            return raw
    return None


def check_compliance(
    water_id: str | None = None,
    water_type: str | None = None,
    fishing_date: date | datetime | None = None,
    gear_id: str | None = None,
) -> ComplianceResult:
    """合规前置检查.

    Args:
        water_id: 水域 ID (e.g. "miyun_reservoir", "yongding_river")
        water_type: 水域类型 (river/reservoir/black_pit)
        fishing_date: 垂钓日期，None 则用当前 BJ_TZ 日期
        gear_id: 渔具 ID (e.g. "single_hook_rod", "electric_fishing")

    Returns:
        ComplianceResult: block_analysis=True 时分析应被拦截

    检查顺序（优先级从高到低）:
        1. 饮用水源保护区 → 全年禁钓
        2. 天然水域禁渔期 → 04-01~07-31，黑坑豁免
        3. 禁用渔具 → 刑事/行政处罚
    """
    if fishing_date is None:
        fishing_date = datetime.now(BJ_TZ)
    if isinstance(fishing_date, datetime):
        fishing_date = fishing_date.date()
    assert isinstance(fishing_date, date)

    cfg = _load_compliance()
    waters = _load_waters()
    resolved_wt = _get_water_type(water_id, water_type, waters)

    meta = cfg.get("meta", {})
    version = str(meta.get("version", "unknown"))
    effective_date = str(meta.get("effective_date", "unknown"))
    reminders: list[str] = list(cfg.get("compliance_reminders", []))

    reasons: list[str] = []
    closed_season_active = False

    # 1. 饮用水源保护区（全年禁钓）
    if _is_drinking_water_source(water_id, waters) or _is_year_round_banned(water_id, waters):
        w = waters.get(water_id or "", {})
        name = w.get("name", water_id or "未知水域")
        reason_msg = str(w.get("note", f"{name}为饮用水源保护区，全年禁止垂钓"))
        reasons.append(reason_msg)

    # 2. 天然水域禁渔期（黑坑豁免）
    natural_cfg = cfg.get("closed_season", {})
    nw = natural_cfg.get("natural_waters", {})
    affected_types: list[str] = nw.get("affected_water_types", ["river", "reservoir"])
    exempt_types: list[str] = nw.get("exempt_water_types", ["black_pit"])

    is_exempt = resolved_wt in exempt_types
    if not is_exempt and resolved_wt in affected_types:
        if _is_in_closed_season(fishing_date, natural_cfg):
            closed_season_active = True
            season_info = get_season(fishing_date)
            cs_reason = str(nw.get("reason", "鱼类繁殖期保护"))
            reasons.append(
                f"天然水域禁渔期({nw.get('start', '04-01')}~"
                f"{nw.get('end', '07-31')})：{season_info.name}季节，{cs_reason}"
            )

    # 3. 禁用渔具
    if _is_banned_gear(gear_id, cfg):
        for g in cfg.get("banned_gear", []):
            if g.get("id") == gear_id:
                g_name = str(g.get("name", gear_id or "未知"))
                g_penalty = str(g.get("penalty", "违法"))
                reasons.append(f"禁用渔具：{g_name} — {g_penalty}")
                break

    return ComplianceResult(
        block_analysis=len(reasons) > 0,
        reasons=reasons,
        compliance_notes=reminders,
        effective_date=effective_date,
        version=version,
        closed_season_active=closed_season_active,
        water_type=resolved_wt,
        water_id=water_id,
    )
