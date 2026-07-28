"""feedback/storage.py — 渔获反馈 SQLite 存储.

T4.1 完整实现:
  - SQLite 持久化（data/feedback.db）
  - CatchRecord dataclass 统一数据模型
  - CRUD: save / fetch_by_spot / fetch_by_species / fetch_all
  - JSON 导出（供 tuning.py train/eval 消费）
  - 线程安全（每次操作独立 connection）

表 schema:
  catches(id, spot_name, species, actual_rating, weight_kg, count,
          bait, fishing_score, created_at)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config import BJ_TZ, PROJECT_ROOT, settings

logger = logging.getLogger(__name__)


# ============================================================================
# 数据模型
# ============================================================================

@dataclass(frozen=True)
class CatchRecord:
    """单条渔获反馈记录."""

    spot_name: str
    species: str
    actual_rating: int  # 1-5
    weight_kg: float | None = None
    count: int | None = None
    bait: str | None = None
    fishing_score: float | None = None  # 系统给出的评分 (0-1)
    id: int | None = None
    created_at: datetime | None = None


# ============================================================================
# 存储引擎
# ============================================================================

_DB_PATH = PROJECT_ROOT / settings.feedback_db_path


def _get_conn() -> sqlite3.Connection:
    """获取 SQLite 连接（每次调用新建，确保线程安全）."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    """初始化表结构（幂等）."""
    conn = _get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spot_name TEXT NOT NULL,
                species TEXT NOT NULL,
                actual_rating INTEGER NOT NULL CHECK(actual_rating BETWEEN 1 AND 5),
                weight_kg REAL,
                count INTEGER,
                bait TEXT,
                fishing_score REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_catches_spot ON catches(spot_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_catches_species ON catches(species)"
        )
        conn.commit()
        logger.debug("storage: db initialized at %s", _DB_PATH)
    finally:
        conn.close()


def save_catch(record: CatchRecord) -> int:
    """保存渔获记录，返回新行 ID.

    Args:
        record: CatchRecord 数据对象

    Returns:
        新插入行的 ID
    """
    _init_db()
    now = record.created_at or datetime.now(BJ_TZ)
    conn = _get_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO catches
                (spot_name, species, actual_rating, weight_kg, count,
                 bait, fishing_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.spot_name,
                record.species,
                record.actual_rating,
                record.weight_kg,
                record.count,
                record.bait,
                record.fishing_score,
                now.isoformat(),
            ),
        )
        conn.commit()
        row_id = cursor.lastrowid
        assert row_id is not None
        logger.info(
            "storage: saved catch id=%d spot=%s species=%s rating=%d",
            row_id, record.spot_name, record.species, record.actual_rating,
        )
        return row_id
    finally:
        conn.close()


def _row_to_record(row: sqlite3.Row) -> CatchRecord:
    """将数据库行转为 CatchRecord."""
    created_str = row["created_at"]
    created_dt: datetime | None = None
    if created_str:
        try:
            created_dt = datetime.fromisoformat(created_str)
        except ValueError:
            created_dt = None

    return CatchRecord(
        id=row["id"],
        spot_name=row["spot_name"],
        species=row["species"],
        actual_rating=row["actual_rating"],
        weight_kg=row["weight_kg"],
        count=row["count"],
        bait=row["bait"],
        fishing_score=row["fishing_score"],
        created_at=created_dt,
    )


def fetch_by_spot(spot_name: str, limit: int = 100) -> list[CatchRecord]:
    """按钓点查询历史渔获."""
    _init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM catches WHERE spot_name = ? ORDER BY created_at DESC LIMIT ?",
            (spot_name, limit),
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def fetch_by_species(species: str, limit: int = 100) -> list[CatchRecord]:
    """按鱼种查询历史渔获."""
    _init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM catches WHERE species = ? ORDER BY created_at DESC LIMIT ?",
            (species, limit),
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def fetch_all(limit: int = 1000) -> list[CatchRecord]:
    """查询全部历史渔获."""
    _init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM catches ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def fetch_recent(limit: int = 10) -> list[CatchRecord]:
    """查询最近的 N 条渔获（用于 collect.py 自动填充）."""
    _init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM catches ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def count_records() -> int:
    """返回总记录数."""
    _init_db()
    conn = _get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM catches").fetchone()
        return int(row["cnt"])
    finally:
        conn.close()


# ============================================================================
# JSON 导出（供 tuning.py 消费）
# ============================================================================

def export_json(path: str | Path | None = None) -> Path:
    """导出全部记录为 JSON 文件.

    Args:
        path: 目标路径，None=默认 data/feedback_export.json

    Returns:
        实际写入的文件路径
    """
    records = fetch_all(limit=100000)
    out_path = Path(path) if path else PROJECT_ROOT / "data" / "feedback_export.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data: list[dict[str, Any]] = []
    for r in records:
        d = asdict(r)
        if r.created_at is not None:
            d["created_at"] = r.created_at.isoformat()
        data.append(d)

    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("storage: exported %d records to %s", len(data), out_path)
    return out_path


def to_dict_list(records: list[CatchRecord]) -> list[dict[str, Any]]:
    """将 CatchRecord 列表转为可序列化 dict 列表."""
    out: list[dict[str, Any]] = []
    for r in records:
        d = asdict(r)
        if r.created_at is not None:
            d["created_at"] = r.created_at.isoformat()
        out.append(d)
    return out
