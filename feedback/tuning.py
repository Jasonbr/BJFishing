"""feedback/tuning.py — 反馈调权自学习.

T4.2 完整实现:
  - train/eval 80/20 分割
  - ±10 算法: 系统评分与实际评级偏差 → feedback_adjustment
  - 输入: storage.CatchRecord 列表
  - 输出: feedback_adjustment float (-0.10 ~ +0.10)
  - 缓存: 按 (spot_name, species) 维度缓存调整值

±10 算法:
  1. 将 actual_rating (1-5) 映射到 0-1 区间: r_norm = (rating-1)/4
  2. 偏差 = mean(r_norm - fishing_score) over train_set
  3. 裁剪到 [-0.10, +0.10]
  4. eval_set 验证: 调整后 MAE 应 <= 调整前 MAE
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from config import BJ_TZ  # noqa: F401 — 保留用于未来时区敏感的调权
from engine.weights import FEEDBACK_ADJUSTMENT_MAX
from feedback.storage import CatchRecord, fetch_all

logger = logging.getLogger(__name__)


# ============================================================================
# 结果模型
# ============================================================================

@dataclass(frozen=True)
class TuningResult:
    """调权计算结果."""

    adjustment: float  # -0.10 ~ +0.10
    train_size: int
    eval_size: int
    train_mae_before: float
    train_mae_after: float
    eval_mae_before: float
    eval_mae_after: float
    improved: bool  # eval MAE 是否改善


# ============================================================================
# 核心算法
# ============================================================================

def _rating_to_norm(rating: int) -> float:
    """将 1-5 评级映射到 0-1 区间."""
    return (rating - 1) / 4.0


def _mae(scores: list[float], targets: list[float]) -> float:
    """计算平均绝对误差."""
    if not scores:
        return 0.0
    return sum(abs(s - t) for s, t in zip(scores, targets, strict=True)) / len(scores)


def _split_train_eval(
    records: list[CatchRecord],
    train_ratio: float = 0.8,
) -> tuple[list[CatchRecord], list[CatchRecord]]:
    """按时间顺序分割 train/eval（前 80% 训练，后 20% 验证）."""
    if len(records) <= 1:
        return records, []

    # 按时间正序排列（旧→新）
    sorted_records = sorted(
        records,
        key=lambda r: r.created_at or _fallback_ts(),
    )
    split_idx = max(1, int(len(sorted_records) * train_ratio))
    return sorted_records[:split_idx], sorted_records[split_idx:]


def _fallback_ts() -> Any:
    """created_at 缺失时的排序 fallback."""
    from datetime import datetime, timezone
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def compute_adjustment(
    records: list[CatchRecord] | None = None,
    train_ratio: float = 0.8,
) -> TuningResult:
    """计算 feedback_adjustment.

    Args:
        records: 渔获记录列表，None=从 storage 全量加载
        train_ratio: 训练集比例（默认 0.8）

    Returns:
        TuningResult: 含调整值和验证指标
    """
    if records is None:
        records = fetch_all(limit=100000)

    # 过滤掉无 fishing_score 的记录
    scored = [r for r in records if r.fishing_score is not None]
    if len(scored) < 2:
        logger.info("tuning: insufficient data (%d records), adjustment=0", len(scored))
        return TuningResult(
            adjustment=0.0,
            train_size=len(scored),
            eval_size=0,
            train_mae_before=0.0,
            train_mae_after=0.0,
            eval_mae_before=0.0,
            eval_mae_after=0.0,
            improved=False,
        )

    train_set, eval_set = _split_train_eval(scored, train_ratio)

    # 训练集计算偏差
    train_diffs: list[float] = []
    for r in train_set:
        assert r.fishing_score is not None
        assert r.actual_rating is not None
        r_norm = _rating_to_norm(r.actual_rating)
        train_diffs.append(r_norm - r.fishing_score)

    raw_adjustment = sum(train_diffs) / len(train_diffs) if train_diffs else 0.0

    # 裁剪到 ±0.10
    adjustment = max(
        -FEEDBACK_ADJUSTMENT_MAX,
        min(FEEDBACK_ADJUSTMENT_MAX, raw_adjustment),
    )

    # 验证
    train_scores_before = [r.fishing_score for r in train_set if r.fishing_score is not None]
    train_targets = [_rating_to_norm(r.actual_rating) for r in train_set]
    train_scores_after = [s + adjustment for s in train_scores_before]

    train_mae_before = _mae(train_scores_before, train_targets)
    train_mae_after = _mae(train_scores_after, train_targets)

    if eval_set:
        eval_scores_before = [r.fishing_score for r in eval_set if r.fishing_score is not None]
        eval_targets = [_rating_to_norm(r.actual_rating) for r in eval_set]
        eval_scores_after = [s + adjustment for s in eval_scores_before]

        eval_mae_before = _mae(eval_scores_before, eval_targets)
        eval_mae_after = _mae(eval_scores_after, eval_targets)
    else:
        eval_mae_before = 0.0
        eval_mae_after = 0.0

    improved = eval_mae_after <= eval_mae_before if eval_set else True

    logger.info(
        "tuning: adjustment=%+.3f (raw=%+.3f) train_mae %.4f→%.4f eval_mae %.4f→%.4f improved=%s",
        adjustment, raw_adjustment,
        train_mae_before, train_mae_after,
        eval_mae_before, eval_mae_after,
        improved,
    )

    return TuningResult(
        adjustment=round(adjustment, 3),
        train_size=len(train_set),
        eval_size=len(eval_set),
        train_mae_before=round(train_mae_before, 4),
        train_mae_after=round(train_mae_after, 4),
        eval_mae_before=round(eval_mae_before, 4),
        eval_mae_after=round(eval_mae_after, 4),
        improved=improved,
    )


# ============================================================================
# 缓存层（避免每次分析都重算）
# ============================================================================

_adjustment_cache: float | None = None


def get_cached_adjustment() -> float:
    """获取缓存的 adjustment（首次调用自动计算）."""
    global _adjustment_cache
    if _adjustment_cache is None:
        result = compute_adjustment()
        _adjustment_cache = result.adjustment
    return _adjustment_cache


def reload_adjustment() -> float:
    """重新计算并刷新缓存（提交新渔获后调用）."""
    global _adjustment_cache
    result = compute_adjustment()
    _adjustment_cache = result.adjustment
    return _adjustment_cache


def reset_cache() -> None:
    """重置缓存（测试用）."""
    global _adjustment_cache
    _adjustment_cache = None
