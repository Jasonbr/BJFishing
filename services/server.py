"""BJFishing MCP Server — 北京钓鱼分析 MCP 服务入口.

注册 4 个 Tool（T0.5 骨架，T0.6a-d 填充实现）:
  - collect_conditions: 采集天气/月相/历史均温等环境数据
  - analyze_fishing: 多维评分（气压/温度/月相/风/降水/季节/水温/溶氧）+ 合规拦截
  - report_fishing: 生成钓鱼报告（JSON / Qwen / Ollama 三模式）
  - submit_catch: 提交渔获反馈（SQLite 存储，用于自学习调权）

传输: stdio
日志: 结构化 logging（JSON formatter 可配）
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP Server 实例
# ---------------------------------------------------------------------------

server: Server = Server(
    name=settings.mcp_server_name,
    version=settings.mcp_server_version,
    instructions=(
        "BJFishing 北京钓鱼分析 MCP Server。"
        "提供钓鱼条件采集、鱼情评分、策略报告、渔获反馈 4 个 Tool。"
        "所有时间均为北京时区（Asia/Shanghai）。"
    ),
)

# ---------------------------------------------------------------------------
# Tool 定义（T0.5 骨架 — T0.6a-d 将替换为真实调用）
# ---------------------------------------------------------------------------

_TOOL_DEFS: list[types.Tool] = [
    types.Tool(
        name="collect_conditions",
        description=(
            "采集钓点环境数据：实时天气（Open-Meteo forecast）、"
            "天文数据（月相/日出日落/黄金时刻，Astral）、"
            "历史 N 日均温（Open-Meteo archive）。"
            "输入: spot_name(钓点名) 或 lat/lng 坐标。"
            "输出: 含 weather/astronomy/historical_avg_temp 的 JSON。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "spot_name": {
                    "type": "string",
                    "description": "钓点名称（如 '密云水库'），与 lat/lng 二选一",
                },
                "lat": {
                    "type": "number",
                    "description": "纬度，与 spot_name 二选一",
                },
                "lng": {
                    "type": "number",
                    "description": "经度，与 spot_name 二选一",
                },
                "historical_days": {
                    "type": "integer",
                    "description": "历史均温回溯天数（默认 3）",
                    "default": 3,
                },
            },
        },
    ),
    types.Tool(
        name="analyze_fishing",
        description=(
            "综合分析鱼情：8 维评分（气压/温度/月相/风/降水/季节/水温/溶氧）"
            "+ 合规拦截（禁渔期/饮用水源→block_analysis=true）"
            "+ 动态权重 + 反馈调权。"
            "输入: spot_name 或 lat/lng。"
            "输出: 0-100 fishing_score + 各维度子分 + 合规状态。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "spot_name": {
                    "type": "string",
                    "description": "钓点名称",
                },
                "lat": {"type": "number", "description": "纬度"},
                "lng": {"type": "number", "description": "经度"},
                "water_type": {
                    "type": "string",
                    "enum": ["river", "reservoir", "black_pit"],
                    "description": "水域类型（river/reservoir/black_pit）",
                },
            },
        },
    ),
    types.Tool(
        name="report_fishing",
        description=(
            "生成钓鱼策略报告：钓位选择、饵料推荐、时间窗口、风险提示、合规免责。"
            "支持 3 种输出模式: json（纯结构化）/ qwen（通义千问 API）/ ollama（本地大模型）。"
            "多 LLM 自动切换: Qwen 失败→Ollama→JSON fallback。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "spot_name": {"type": "string", "description": "钓点名称"},
                "lat": {"type": "number", "description": "纬度"},
                "lng": {"type": "number", "description": "经度"},
                "output_mode": {
                    "type": "string",
                    "enum": ["json", "qwen", "ollama"],
                    "default": "json",
                    "description": "输出模式",
                },
                "analysis_result": {
                    "type": "object",
                    "description": "已有分析结果（可选，避免重复计算）",
                },
            },
        },
    ),
    types.Tool(
        name="submit_catch",
        description=(
            "提交渔获反馈: 钓点/鱼种/重量/数量/时间/饵料/评分校准。"
            "存储到 SQLite，用于自学习调权（train/eval 80/20 + ±10 算法）。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "spot_name": {"type": "string", "description": "钓点名称"},
                "species": {"type": "string", "description": "鱼种（如 '鲫鱼'）"},
                "weight_kg": {"type": "number", "description": "总重量(kg)"},
                "count": {"type": "integer", "description": "尾数"},
                "bait": {"type": "string", "description": "使用的饵料"},
                "fishing_score": {"type": "number", "description": "系统给出的评分(0-100)"},
                "actual_rating": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "实际钓况评级(1-5)",
                },
            },
            "required": ["spot_name", "species", "actual_rating"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """返回所有注册的 Tool 定义."""
    logger.debug("list_tools called, returning %d tools", len(_TOOL_DEFS))
    return _TOOL_DEFS


@server.call_tool()
async def call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    """Tool 路由分发 — T0.5 骨架返回 stub JSON.

    T0.6a-d 将替换为真实 tools/* 模块调用.
    """
    arguments = arguments or {}
    logger.info("call_tool: name=%s args=%s", name, json.dumps(arguments, ensure_ascii=False))

    # T0.5 stub: 返回占位 JSON
    stub_response: dict[str, Any] = {
        "tool": name,
        "status": "stub",
        "message": f"Tool '{name}' 尚未实现（T0.5 骨架）。将在 T0.6a-d / P3 / P4 填充。",
        "received_args": arguments,
    }

    return [types.TextContent(type="text", text=json.dumps(stub_response, ensure_ascii=False, indent=2))]


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


async def main() -> None:
    """MCP Server stdio 入口."""
    init_options = server.create_initialization_options(
        notification_options=NotificationOptions(tools_changed=False),
    )
    async with stdio_server() as (read_stream, write_stream):
        logger.info(
            "BJFishing MCP Server starting: name=%s version=%s",
            settings.mcp_server_name,
            settings.mcp_server_version,
        )
        await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()
    asyncio.run(main())
