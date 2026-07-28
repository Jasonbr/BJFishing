"""cli.py — BJFishing CLI 入口（T5.3 升级版）.

支持 5 个子命令:
  collect  — 采集钓点环境数据
  analyze  — 综合分析鱼情评分
  report   — 生成策略报告（JSON / Qwen / Ollama）
  submit   — 提交渔获反馈（触发自学习调权）
  tune     — 查看调权状态 / 重算调整值

Usage:
    python cli.py collect --spot-name "密云水库"
    python cli.py analyze --spot-name "温榆河顺义段" --water-type river
    python cli.py report --spot-name "温榆河顺义段" --output-mode json
    python cli.py submit --spot-name "温榆河" --species "crucian_carp" --rating 4 --score 0.72
    python cli.py tune
    python cli.py tune --export /tmp/catches.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from typing import Any

from config import BJ_TZ
from feedback.storage import count_records, export_json
from feedback.tuning import compute_adjustment, get_cached_adjustment
from tools.analyze import analyze_fishing
from tools.collect import collect_conditions
from tools.report import report_fishing
from tools.submit_catch import submit_catch

logger = logging.getLogger(__name__)

_VERSION = "0.2.0"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _add_loc_args(parser: argparse.ArgumentParser) -> None:
    """Add shared --spot-name / --lat / --lng arguments to a subparser."""
    parser.add_argument("--spot-name", type=str, default=None, help="钓点名称（与 --lat/--lng 二选一）")
    parser.add_argument("--lat", type=float, default=None, help="纬度（与 --spot-name 二选一）")
    parser.add_argument("--lng", type=float, default=None, help="经度（与 --spot-name 二选一）")


def _add_water_args(parser: argparse.ArgumentParser) -> None:
    """Add --water-type and --species-id arguments."""
    parser.add_argument("--water-type", type=str, default=None, choices=["river", "reservoir", "black_pit"], help="水域类型")
    parser.add_argument("--species-id", type=str, default=None, help="鱼种ID（crucian_carp/common_carp/grass_carp/silver_carp/bighead_carp）")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 CLI 参数."""
    parser = argparse.ArgumentParser(
        prog="bjfishing",
        description="北京钓鱼分析 CLI（collect / analyze / report / submit / tune）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- collect ---
    p_collect = sub.add_parser("collect", help="采集钓点环境数据")
    _add_loc_args(p_collect)
    p_collect.add_argument("--historical-days", type=int, default=3, help="历史均温回溯天数（默认 3）")

    # --- analyze ---
    p_analyze = sub.add_parser("analyze", help="综合分析鱼情评分")
    _add_loc_args(p_analyze)
    _add_water_args(p_analyze)
    p_analyze.add_argument("--feedback-adjustment", type=float, default=None, help="手动指定反馈调权（±0.10），默认从 tuning 缓存读取")

    # --- report ---
    p_report = sub.add_parser("report", help="生成策略报告")
    _add_loc_args(p_report)
    _add_water_args(p_report)
    p_report.add_argument("--output-mode", type=str, default="json", choices=["json", "qwen", "ollama"], help="输出模式（默认 json）")

    # --- submit ---
    p_submit = sub.add_parser("submit", help="提交渔获反馈")
    p_submit.add_argument("--spot-name", type=str, required=True, help="钓点名称")
    p_submit.add_argument("--species", type=str, required=True, help="鱼种")
    p_submit.add_argument("--rating", type=int, required=True, help="实际钓况评级 (1-5)")
    p_submit.add_argument("--weight-kg", type=float, default=None, help="总重量(kg)")
    p_submit.add_argument("--count", type=int, default=None, help="尾数")
    p_submit.add_argument("--bait", type=str, default=None, help="使用的饵料")
    p_submit.add_argument("--score", type=float, default=None, help="系统给出的评分 (0-1)，用于自学习调权")

    # --- tune ---
    p_tune = sub.add_parser("tune", help="查看调权状态 / 重算 / 导出")
    p_tune.add_argument("--export", type=str, default=None, help="导出渔获记录到 JSON 文件路径")
    p_tune.add_argument("--recompute", action="store_true", help="强制重算调整值（忽略缓存）")

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_loc_args(args: argparse.Namespace) -> list[str]:
    """Validate --spot-name / --lat / --lng combination."""
    errors: list[str] = []
    if args.spot_name is None and (args.lat is None or args.lng is None):
        errors.append("必须提供 --spot-name 或 --lat + --lng")
    if (args.lat is not None and args.lng is None) or (args.lat is None and args.lng is not None):
        errors.append("--lat 和 --lng 必须同时提供")
    if args.lat is not None and not (-90 <= args.lat <= 90):
        errors.append(f"--lat 范围 -90~90, got {args.lat}")
    if args.lng is not None and not (-180 <= args.lng <= 180):
        errors.append(f"--lng 范围 -180~180, got {args.lng}")
    return errors


def _validate_collect_args(args: argparse.Namespace) -> list[str]:
    """校验 collect 子命令参数."""
    errors = _validate_loc_args(args)
    if args.historical_days < 1 or args.historical_days > 30:
        errors.append(f"--historical-days 范围 1~30, got {args.historical_days}")
    return errors


def _validate_submit_args(args: argparse.Namespace) -> list[str]:
    """校验 submit 子命令参数."""
    errors: list[str] = []
    if not (1 <= args.rating <= 5):
        errors.append(f"--rating 范围 1~5, got {args.rating}")
    if args.weight_kg is not None and args.weight_kg < 0:
        errors.append(f"--weight-kg 不能为负, got {args.weight_kg}")
    if args.count is not None and args.count < 0:
        errors.append(f"--count 不能为负, got {args.count}")
    if args.score is not None and not (0.0 <= args.score <= 1.0):
        errors.append(f"--score 范围 0~1, got {args.score}")
    return errors


# ---------------------------------------------------------------------------
# Command runners
# ---------------------------------------------------------------------------

async def _run_collect(args: argparse.Namespace) -> dict[str, Any]:
    """执行 collect 子命令."""
    result = await collect_conditions(
        spot_name=args.spot_name,
        lat=args.lat,
        lng=args.lng,
        historical_days=args.historical_days,
    )
    result["_cli"] = {"command": "collect", "timestamp": datetime.now(BJ_TZ).isoformat(), "version": _VERSION}
    return result


async def _run_analyze(args: argparse.Namespace) -> dict[str, Any]:
    """执行 analyze 子命令."""
    result = await analyze_fishing(
        spot_name=args.spot_name,
        lat=args.lat,
        lng=args.lng,
        water_type=args.water_type,
        species_id=args.species_id,
        feedback_adjustment=args.feedback_adjustment,
    )
    result["_cli"] = {"command": "analyze", "timestamp": datetime.now(BJ_TZ).isoformat(), "version": _VERSION}
    return result


async def _run_report(args: argparse.Namespace) -> dict[str, Any]:
    """执行 report 子命令."""
    result = await report_fishing(
        spot_name=args.spot_name,
        lat=args.lat,
        lng=args.lng,
        output_mode=args.output_mode,
        water_type=args.water_type,
    )
    result["_cli"] = {"command": "report", "timestamp": datetime.now(BJ_TZ).isoformat(), "version": _VERSION}
    return result


async def _run_submit(args: argparse.Namespace) -> dict[str, Any]:
    """执行 submit 子命令."""
    result = await submit_catch(
        spot_name=args.spot_name,
        species=args.species,
        actual_rating=args.rating,
        weight_kg=args.weight_kg,
        count=args.count,
        bait=args.bait,
        fishing_score=args.score,
    )
    result["_cli"] = {"command": "submit", "timestamp": datetime.now(BJ_TZ).isoformat(), "version": _VERSION}
    return result


def _run_tune(args: argparse.Namespace) -> dict[str, Any]:
    """执行 tune 子命令（同步）."""
    total = count_records()
    cached = get_cached_adjustment()

    if args.export:
        path = export_json(args.export)
        return {
            "command": "tune",
            "action": "export",
            "export_path": str(path),
            "total_records": total,
            "cached_adjustment": cached,
        }

    if args.recompute:
        result = compute_adjustment()
        return {
            "command": "tune",
            "action": "recompute",
            "total_records": total,
            "adjustment": result.adjustment,
            "train_size": result.train_size,
            "eval_size": result.eval_size,
            "mae_before": result.eval_mae_before,
            "mae_after": result.eval_mae_after,
            "improved": result.improved,
        }

    return {
        "command": "tune",
        "action": "status",
        "total_records": total,
        "cached_adjustment": cached,
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    """分发子命令."""
    if args.command == "collect":
        errors = _validate_collect_args(args)
        if errors:
            return {"error": "argument_validation_failed", "details": errors}
        return await _run_collect(args)

    if args.command == "analyze":
        errors = _validate_loc_args(args)
        if errors:
            return {"error": "argument_validation_failed", "details": errors}
        return await _run_analyze(args)

    if args.command == "report":
        errors = _validate_loc_args(args)
        if errors:
            return {"error": "argument_validation_failed", "details": errors}
        return await _run_report(args)

    if args.command == "submit":
        errors = _validate_submit_args(args)
        if errors:
            return {"error": "argument_validation_failed", "details": errors}
        return await _run_submit(args)

    if args.command == "tune":
        return _run_tune(args)

    return {"error": f"unknown_command: {args.command}"}


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI 主入口."""
    from logging_config import setup_logging
    setup_logging()
    args = _parse_args(argv)
    try:
        if args.command == "tune":
            result: dict[str, Any] = _run_tune(args)
        else:
            result = asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        print(json.dumps({"error": "interrupted"}, ensure_ascii=False))
        return 130
    except Exception as exc:
        logger.exception("CLI failed")
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
