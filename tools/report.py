"""tools/report.py — 生成钓鱼策略报告.

T3.4a-c 完整实现:
  - T3.4a: JSON 输出（含 blocked 响应 schema + disclaimer 免责声明）
  - T3.4b: Qwen API 输出
  - T3.4c: Ollama + 多 LLM 自动切换（Qwen 失败→Ollama→JSON fallback）
  - 策略模块: strategy/position.py / bait.py / time_windows.py / risk.py
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from config import settings
from strategy.bait import recommend_bait
from strategy.position import recommend_position
from strategy.risk import assess_risk
from strategy.time_windows import recommend_time_windows
from tools.analyze import analyze_fishing

logger = logging.getLogger(__name__)

_DISCLAIMER = (
    "本报告由 AI 生成，仅供参考。钓鱼活动请遵守当地法律法规，"
    "注意人身安全，不在禁渔期/禁渔区/饮用水源保护区垂钓。"
)


async def report_fishing(
    spot_name: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    output_mode: str = "json",
    analysis_result: dict[str, Any] | None = None,
    water_type: str | None = None,
) -> dict[str, Any]:
    """生成钓鱼策略报告.

    Args:
        spot_name: 钓点名称
        lat: 纬度
        lng: 经度
        output_mode: 输出模式 (json/qwen/ollama)
        analysis_result: 已有分析结果（可选，避免重复计算）
        water_type: 水域类型

    Returns:
        含 report / strategy / disclaimer 的 dict

    多 LLM 自动切换:
        qwen 失败 → ollama → json fallback
    """
    # --- 1. 获取分析结果 ---
    if analysis_result is None:
        analysis_result = await analyze_fishing(
            spot_name=spot_name, lat=lat, lng=lng, water_type=water_type,
        )

    # --- 2. 合规拦截 → blocked 响应 ---
    compliance = analysis_result.get("compliance", {})
    if compliance.get("block_analysis"):
        logger.info("report: blocked by compliance")
        return _build_blocked_response(analysis_result, output_mode)

    # --- 3. 降级模式 → 仅返回结构化数据 ---
    if analysis_result.get("data_quality") == "degraded":
        logger.info("report: degraded mode, JSON only")
        return _build_json_report(analysis_result, "degraded")

    # --- 4. 生成策略 ---
    strategy = {
        "position": recommend_position(analysis_result),
        "bait": recommend_bait(analysis_result),
        "time_windows": recommend_time_windows(analysis_result),
        "risk": assess_risk(analysis_result),
    }

    # --- 5. 按输出模式生成报告 ---
    if output_mode == "json":
        return _build_json_report(analysis_result, "full", strategy)

    # LLM 模式: qwen / ollama, 带 auto-fallback
    llm_report, llm_status = _generate_llm_report(
        analysis_result, strategy, output_mode,
    )

    return _build_json_report(analysis_result, "full", strategy, llm_report, llm_status)


# ---------- JSON 报告 ----------


def _build_json_report(
    analysis_result: dict[str, Any],
    quality: str,
    strategy: dict[str, Any] | None = None,
    llm_report: str | None = None,
    llm_status: str | None = None,
) -> dict[str, Any]:
    """T3.4a: 构建 JSON 结构化报告."""
    report: dict[str, Any] = {
        "spot_name": analysis_result.get("spot_name"),
        "lat": analysis_result.get("lat"),
        "lng": analysis_result.get("lng"),
        "water_type": analysis_result.get("water_type"),
        "fishing_score": analysis_result.get("fishing_score"),
        "sub_scores": analysis_result.get("sub_scores", {}),
        "data_quality": analysis_result.get("data_quality"),
        "confidence": analysis_result.get("confidence", "none"),
        "compliance": analysis_result.get("compliance", {}),
        "strategy": strategy or {},
        "llm_report": llm_report,
        "llm_status": llm_status or ("json_mode" if llm_report is None and quality != "degraded" else None),
        "disclaimer": _DISCLAIMER,
        "generated_at": analysis_result.get("analyzed_at"),
    }
    return report


def _build_blocked_response(
    analysis_result: dict[str, Any],
    output_mode: str,
) -> dict[str, Any]:
    """T3.4a: 合规拦截响应 schema."""
    compliance = analysis_result.get("compliance", {})
    reasons = compliance.get("reasons", [])
    notes = compliance.get("compliance_notes", [])

    # 构建替代建议
    alternatives: list[str] = []
    if any("禁渔期" in r for r in reasons):
        alternatives.append("建议8月1日禁渔期结束后再钓")
        alternatives.append("可前往商业黑坑（全年开放）")
    if any("饮用水" in r for r in reasons):
        alternatives.append("请前往非饮用水源水域")
    if any("渔具" in r or "渔法" in r for r in reasons):
        alternatives.append("请更换为合规渔具（单钩手竿）")
    if not alternatives:
        alternatives.append("请咨询当地渔政部门")

    return {
        "status": "blocked",
        "reason": "; ".join(reasons) if reasons else "合规拦截",
        "compliance_notes": notes,
        "alternative_suggestions": alternatives,
        "compliance_detail": compliance,
        "disclaimer": _DISCLAIMER,
        "generated_at": analysis_result.get("analyzed_at"),
    }


# ---------- LLM 报告 ----------


def _generate_llm_report(
    analysis_result: dict[str, Any],
    strategy: dict[str, Any],
    requested_mode: str,
) -> tuple[str | None, str]:
    """多 LLM 自动切换: qwen 失败 → ollama → None (JSON fallback).

    Args:
        requested_mode: "qwen" or "ollama"

    Returns:
        (llm_report_text, status_message)
        status_message 解释结果，如 "qwen:success" / "qwen:no_api_key, ollama:connection_failed"
    """
    prompt = _build_llm_prompt(analysis_result, strategy)
    failures: list[str] = []

    # 按请求模式优先调用
    if requested_mode == "qwen":
        # 1. 尝试 Qwen
        result = _call_qwen(prompt)
        if result:
            logger.info("LLM: qwen success")
            return result, "qwen:success"
        failures.append("qwen:failed")
        # 2. Qwen 失败 → Ollama
        logger.warning("LLM: qwen failed, falling back to ollama")
        result = _call_ollama(prompt)
        if result:
            logger.info("LLM: ollama fallback success")
            return result, "ollama:fallback_success"
        failures.append("ollama:failed")

    elif requested_mode == "ollama":
        # 1. 尝试 Ollama
        result = _call_ollama(prompt)
        if result:
            logger.info("LLM: ollama success")
            return result, "ollama:success"
        failures.append("ollama:failed")
        # 2. Ollama 失败 → Qwen
        logger.warning("LLM: ollama failed, falling back to qwen")
        result = _call_qwen(prompt)
        if result:
            logger.info("LLM: qwen fallback success")
            return result, "qwen:fallback_success"
        failures.append("qwen:failed")

    # 3. 都失败 → JSON fallback
    logger.warning("LLM: all LLM backends failed, JSON fallback")
    return None, ", ".join(failures) + ", json_fallback"

def _build_llm_prompt(
    analysis_result: dict[str, Any],
    strategy: dict[str, Any],
) -> str:
    """构建 LLM 提示词."""
    score = analysis_result.get("fishing_score")
    sub_scores = analysis_result.get("sub_scores", {})
    spot = analysis_result.get("spot_name", "未知钓点")
    wtype = analysis_result.get("water_type", "river")

    pos = strategy.get("position", {})
    bait = strategy.get("bait", {})
    tw = strategy.get("time_windows", {})
    risk = strategy.get("risk", {})

    prompt = f"""你是一位经验丰富的钓鱼顾问。请根据以下分析数据，用简洁专业的中文给钓友一份钓鱼建议报告。

钓点: {spot} ({wtype})
综合评分: {score:.2f}/1.00 (置信度: {analysis_result.get('confidence', 'unknown')})

各项评分:
- 气压: {sub_scores.get('pressure', 'N/A')}
- 温度: {sub_scores.get('temperature', 'N/A')}
- 月相: {sub_scores.get('solunar', 'N/A')}
- 风况: {sub_scores.get('wind', 'N/A')}
- 降水: {sub_scores.get('precipitation', 'N/A')}
- 季节: {sub_scores.get('season', 'N/A')}
- 水质: {sub_scores.get('water', 'N/A')}

策略建议:
- 钓位: {pos.get('recommendation', 'N/A')} — {pos.get('reason', '')}
- 饵料: {bait.get('primary', 'N/A')} — {bait.get('reason', '')}
- 时段: {tw.get('best_window', {}).get('type', 'N/A') if tw.get('best_window') else 'N/A'}
- 风险: {risk.get('level', 'N/A')} — {', '.join(risk.get('warnings', [])) or '无风险提示'}

请输出：
1. 总体评价（1-2句）
2. 钓位建议（1句）
3. 饵料建议（1句）
4. 最佳时段（1句）
5. 安全提醒（1句）
"""
    return prompt


def _call_qwen(prompt: str) -> str | None:
    """T3.4b: 调用 Qwen API (DashScope 兼容模式)."""
    api_key = settings.qwen_api_key
    base_url = settings.qwen_base_url
    model = settings.qwen_model

    if not api_key:
        logger.warning("qwen: no API key configured")
        return None

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是钓鱼顾问AI, 输出简洁专业的中文建议."},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if isinstance(content, str):
            logger.info("qwen: report generated (%d chars)", len(content))
            return content
        logger.warning("qwen: empty response")
        return None
    except Exception as exc:
        logger.warning("qwen API failed: %s", exc)
        return None


def _call_ollama(prompt: str) -> str | None:
    """T3.4c: 调用 Ollama 本地 LLM."""
    base_url = settings.ollama_url
    model = settings.ollama_model

    url = f"{base_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是钓鱼顾问AI, 输出简洁专业的中文建议."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content")
        if isinstance(content, str):
            logger.info("ollama: report generated (%d chars)", len(content))
            return content
        logger.warning("ollama: empty response")
        return None
    except Exception as exc:
        logger.warning("ollama API failed: %s", exc)
        return None
