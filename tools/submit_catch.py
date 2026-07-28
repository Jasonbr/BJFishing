"""tools/submit_catch.py — 提交渔获反馈.

T4.0 完整实现:
  - 调 feedback/storage.py: SQLite 持久化
  - 调 feedback/tuning.py: 提交后刷新 adjustment 缓存
  - 字段: 钓点/鱼种/重量/数量/时间/饵料/评分校准
  - 返回 stored_id + refreshed adjustment
"""

from __future__ import annotations

import logging
from typing import Any

from feedback.storage import CatchRecord, save_catch
from feedback.tuning import reload_adjustment

logger = logging.getLogger(__name__)


async def submit_catch(
    spot_name: str,
    species: str,
    actual_rating: int,
    weight_kg: float | None = None,
    count: int | None = None,
    bait: str | None = None,
    fishing_score: float | None = None,
) -> dict[str, Any]:
    """提交渔获反馈到本地存储.

    Args:
        spot_name: 钓点名称
        species: 鱼种
        actual_rating: 实际钓况评级 (1-5)
        weight_kg: 总重量(kg)
        count: 尾数
        bait: 使用的饵料
        fishing_score: 系统给出的评分 (0-1)，用于自学习调权

    Returns:
        含 success / stored_id / refreshed_adjustment 的 dict

    Raises:
        ValueError: actual_rating 不在 1-5 范围
    """
    # --- 1. 参数校验 ---
    if not spot_name or not spot_name.strip():
        raise ValueError("spot_name 不能为空")
    if not species or not species.strip():
        raise ValueError("species 不能为空")
    if not 1 <= actual_rating <= 5:
        raise ValueError(f"actual_rating 必须在 1-5 范围, got {actual_rating}")
    if weight_kg is not None and weight_kg < 0:
        raise ValueError(f"weight_kg 不能为负, got {weight_kg}")
    if count is not None and count < 0:
        raise ValueError(f"count 不能为负, got {count}")
    if fishing_score is not None and not 0.0 <= fishing_score <= 1.0:
        raise ValueError(
            f"fishing_score 必须在 0-1 范围, got {fishing_score}",
        )

    # --- 2. 构造记录 ---
    record = CatchRecord(
        spot_name=spot_name.strip(),
        species=species.strip(),
        actual_rating=actual_rating,
        weight_kg=weight_kg,
        count=count,
        bait=bait,
        fishing_score=fishing_score,
    )

    # --- 3. 存储 ---
    stored_id = save_catch(record)
    logger.info(
        "submit_catch: stored id=%d spot=%s species=%s rating=%d score=%s",
        stored_id, spot_name, species, actual_rating, fishing_score,
    )

    # --- 4. 刷新调权缓存 ---
    refreshed_adjustment = reload_adjustment()

    return {
        "status": "success",
        "stored": True,
        "stored_id": stored_id,
        "spot_name": record.spot_name,
        "species": record.species,
        "actual_rating": record.actual_rating,
        "weight_kg": record.weight_kg,
        "count": record.count,
        "bait": record.bait,
        "fishing_score": record.fishing_score,
        "refreshed_adjustment": refreshed_adjustment,
        "message": f"渔获记录已保存 (id={stored_id})，调权已刷新 ({refreshed_adjustment:+.3f})",
    }
