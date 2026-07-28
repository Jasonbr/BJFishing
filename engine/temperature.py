"""engine/temperature.py — 温度评分.

T1.4: 水温+气温综合评分.
原理: 不同鱼种有最适水温范围，偏离则减分.
数据源: species_temp.yaml
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import yaml

from config import KNOWLEDGE_DIR

logger = logging.getLogger(__name__)

_SPECIES_PATH = KNOWLEDGE_DIR / "species_temp.yaml"
_species_cache: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True)
class TemperatureResult:
    """温度评分结果."""

    water_temp_c: float  # 水温
    air_temp_c: float  # 气温
    species_id: str | None  # 目标鱼种（None=通用）
    score: float  # 0-1
    level: str  # optimal/feeding/outside
    note: str


def _load_species() -> dict[str, dict[str, Any]]:
    """加载鱼种适温数据."""
    global _species_cache
    if _species_cache is not None:
        return _species_cache
    try:
        with open(_SPECIES_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        species: dict[str, dict[str, Any]] = {}
        for s in data.get("species", []):
            species[s["id"]] = s
        _species_cache = species
        return _species_cache
    except FileNotFoundError:
        logger.warning("species_temp.yaml not found")
        _species_cache = {}
        return _species_cache


def score_temperature(
    water_temp_c: float,
    air_temp_c: float | None = None,
    species_id: str | None = None,
) -> TemperatureResult:
    """温度评分.

    Args:
        water_temp_c: 水温（℃）
        air_temp_c: 气温（℃），可选
        species_id: 目标鱼种 ID（crucian_carp 等），None=通用评分

    Returns:
        TemperatureResult: 评分 + 等级
    """
    species = _load_species()

    if species_id and species_id in species:
        # 有目标鱼种：用鱼种最适范围
        s = species[species_id]
        opt = s["optimal_temp_c"]
        feed = s["feeding_temp_c"]

        if opt["min"] <= water_temp_c <= opt["max"]:
            score = 1.0
            level = "optimal"
            note = f"{s['name']}最适水温({water_temp_c}C)"
        elif feed["min"] <= water_temp_c <= feed["max"]:
            score = 0.6
            level = "feeding"
            note = f"{s['name']}可摄食但非最适({water_temp_c}C)"
        else:
            score = 0.1
            level = "outside"
            note = f"{s['name']}超出摄食范围({water_temp_c}C)"
    else:
        # 通用评分：基于温度区间
        if 15 <= water_temp_c <= 25:
            score = 1.0
            level = "optimal"
            note = f"通用最适水温({water_temp_c}C)"
        elif 10 <= water_temp_c <= 30:
            score = 0.7
            level = "feeding"
            note = f"可摄食水温({water_temp_c}C)"
        elif 4 <= water_temp_c <= 35:
            score = 0.4
            level = "feeding"
            note = f"边缘水温({water_temp_c}C)"
        else:
            score = 0.1
            level = "outside"
            note = f"极端水温({water_temp_c}C)"

    # 气温修正：极端高温/低温略减分
    if air_temp_c is not None:
        if air_temp_c > 35 or air_temp_c < -5:
            score = max(0.1, score - 0.1)

    logger.info("temperature: water=%.1f species=%s score=%.2f level=%s",
                water_temp_c, species_id, score, level)
    return TemperatureResult(
        water_temp_c=water_temp_c,
        air_temp_c=air_temp_c if air_temp_c is not None else water_temp_c,
        species_id=species_id,
        score=score,
        level=level,
        note=note,
    )
