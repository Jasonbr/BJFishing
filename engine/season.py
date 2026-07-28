"""engine/season.py — 北京季节判断（只判季节，不判禁渔期）.

T1.0: 根据 date 判断 spring/summer/autumn/winter.
禁渔期判断移到 compliance/gate.py（T2.5）.

季节划分（来自 knowledge/season_model.yaml）:
  spring:  03-01 ~ 05-31
  summer:  06-01 ~ 08-31
  autumn:  09-01 ~ 11-15
  winter:  11-16 ~ 02-29 (含闰年 2.29)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from config import BJ_TZ, KNOWLEDGE_DIR

logger = logging.getLogger(__name__)

_SEASON_MODEL_PATH: Path = KNOWLEDGE_DIR / "season_model.yaml"

_season_cache: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True)
class SeasonInfo:
    """季节信息."""

    season: str  # spring/summer/autumn/winter
    name: str  # 春季/夏季/秋季/冬季
    scoring_coefficient: float  # 评分系数
    reason: str  # 系数原因


def _load_season_model() -> dict[str, dict[str, Any]]:
    """加载 season_model.yaml 并缓存."""
    global _season_cache
    if _season_cache is not None:
        return _season_cache
    try:
        with open(_SEASON_MODEL_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning("season_model.yaml not found, using defaults")
        _season_cache = _default_seasons()
        return _season_cache

    seasons: dict[str, dict[str, Any]] = {}
    scoring = data.get("scoring", {})
    for s in data.get("seasons", []):
        sid = s["id"]
        sc = scoring.get(sid, {})
        seasons[sid] = {
            "name": s["name"],
            "start": s["date_range"]["start"],
            "end": s["date_range"]["end"],
            "scoring_coefficient": sc.get("coefficient", 0.5),
            "reason": sc.get("reason", ""),
        }
    if len(seasons) != 4:
        logger.warning("Expected 4 seasons, got %d", len(seasons))
    _season_cache = seasons
    return _season_cache


def _default_seasons() -> dict[str, dict[str, Any]]:
    """硬编码默认值（YAML 加载失败时 fallback）."""
    return {
        "spring": {"name": "春季", "start": "03-01", "end": "05-31", "scoring_coefficient": 0.9, "reason": "产卵期活性高"},
        "summer": {"name": "夏季", "start": "06-01", "end": "08-31", "scoring_coefficient": 0.6, "reason": "高温抑制，溶氧不足"},
        "autumn": {"name": "秋季", "start": "09-01", "end": "11-15", "scoring_coefficient": 1.0, "reason": "贴秋膘，摄食最旺"},
        "winter": {"name": "冬季", "start": "11-16", "end": "02-29", "scoring_coefficient": 0.3, "reason": "低温停食，仅冰钓鲫鱼"},
    }


def _parse_mmdd(s: str) -> tuple[int, int]:
    """解析 'MM-DD' 字符串为 (month, day)."""
    parts = s.split("-")
    return int(parts[0]), int(parts[1])


def _to_ord(s: str, year: int) -> int:
    """将 'MM-DD' 转为 date.toordinal()（便于区间比较）."""
    m, d = _parse_mmdd(s)
    return date(year, m, d).toordinal()


def get_season(input_date: date | datetime | None = None) -> SeasonInfo:
    """判断日期所属季节.

    Args:
        input_date: date/datetime，None 则用当前 BJ_TZ 日期

    Returns:
        SeasonInfo: season / name / scoring_coefficient / reason

    边界 case（来自 T1.9 清单）:
      3.1   → spring  (首日)
      5.31  → spring  (末日)
      6.1   → summer  (首日)
      6.30  → summer
      8.31  → summer  (末日)
      9.1   → autumn  (首日)
      11.15 → autumn  (末日)
      11.16 → winter  (首日)
      12.31 → winter
      1.1   → winter  (跨年)
      2.28  → winter  (平年)
      2.29  → winter  (闰年)
    """
    if input_date is None:
        input_date = datetime.now(BJ_TZ)
    if isinstance(input_date, datetime):
        input_date = input_date.date()
    assert isinstance(input_date, date)

    year = input_date.year
    models = _load_season_model()

    # 冬季跨年：11-16 ~ 02-29（含平年 2.28 和闰年 2.29）
    winter_start = _to_ord(models["winter"]["start"], year)  # 11-16
    # 平年构造 2.29 会报错，需要处理
    try:
        winter_end_ord = _to_ord(models["winter"]["end"], year)
    except ValueError:
        # 平年 2.29 不存在，用 2.28
        winter_end_ord = date(year, 2, 28).toordinal()

    cur_ord = input_date.toordinal()

    # 先判冬季（跨年：当年 11.16~12.31 + 次年 1.1~2.28/29）
    if cur_ord >= winter_start or cur_ord <= winter_end_ord:
        m = models["winter"]
        return SeasonInfo(
            season="winter",
            name=m["name"],
            scoring_coefficient=m["scoring_coefficient"],
            reason=m["reason"],
        )

    # 春季：3.1~5.31
    spring_start = _to_ord(models["spring"]["start"], year)
    spring_end = _to_ord(models["spring"]["end"], year)
    if spring_start <= cur_ord <= spring_end:
        m = models["spring"]
        return SeasonInfo(
            season="spring",
            name=m["name"],
            scoring_coefficient=m["scoring_coefficient"],
            reason=m["reason"],
        )

    # 夏季：6.1~8.31
    summer_start = _to_ord(models["summer"]["start"], year)
    summer_end = _to_ord(models["summer"]["end"], year)
    if summer_start <= cur_ord <= summer_end:
        m = models["summer"]
        return SeasonInfo(
            season="summer",
            name=m["name"],
            scoring_coefficient=m["scoring_coefficient"],
            reason=m["reason"],
        )

    # 秋季：9.1~11.15
    autumn_start = _to_ord(models["autumn"]["start"], year)
    autumn_end = _to_ord(models["autumn"]["end"], year)
    if autumn_start <= cur_ord <= autumn_end:
        m = models["autumn"]
        return SeasonInfo(
            season="autumn",
            name=m["name"],
            scoring_coefficient=m["scoring_coefficient"],
            reason=m["reason"],
        )

    # 理论上不会到这里
    logger.error("Season determination failed for %s", input_date)
    m = models["winter"]
    return SeasonInfo(
        season="winter",
        name=m["name"],
        scoring_coefficient=m["scoring_coefficient"],
        reason=m["reason"],
    )


def is_leap_year(year: int) -> bool:
    """判断闰年（边界 case 支持）."""
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
