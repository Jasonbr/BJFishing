"""BJFishing 全局配置管理。

使用 pydantic-settings 统一管理环境变量和配置项。
所有时区敏感操作必须使用 BJ_TZ 常量，确保北京 UTC+8 一致性。

关键设计决策（v3 修订 #2）：
- 时区统一：BJ_TZ = ZoneInfo("Asia/Shanghai")，解决 Open-Meteo(UTC) / Astral(系统TZ) / 禁渔期判断(本地时间) 的时区混乱
- 配置分层：环境变量 → .env 文件 → 代码默认值
- 类型安全：pydantic-settings 自动类型转换和校验
"""
from __future__ import annotations

from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ============================================================================
# 项目路径常量
# ============================================================================
PROJECT_ROOT = Path(__file__).resolve().parent

# ============================================================================
# 时区常量 — 横切关注点，所有模块必须使用
# ============================================================================
# 北京时区（UTC+8）— 所有日期时间操作的统一时区
BJ_TZ = ZoneInfo("Asia/Shanghai")

# UTC 时区 — Open-Meteo API 返回 UTC，需要转换
UTC = timezone.utc


# ============================================================================
# 全局配置
# ============================================================================
class Settings(BaseSettings):
    """BJFishing 全局配置。

    配置加载优先级（高→低）：
    1. 环境变量
    2. .env 文件
    3. 下方 Field 默认值

    所有配置项都有合理默认值，确保开箱即用（无 .env 也能跑）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === LLM 后端配置（T3.4b/c 使用）===
    llm_backend: str = Field(
        default="json",
        description="LLM 后端选择: qwen | ollama | json (json=纯结构化输出，无需 LLM)",
    )
    qwen_api_key: str = Field(
        default="",
        description="Qwen API key (阿里云 dashscope)",
    )
    qwen_model: str = Field(
        default="qwen-turbo",
        description="Qwen 模型名",
    )
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="Qwen API base URL",
    )
    ollama_url: str = Field(
        default="http://localhost:11434",
        description="Ollama 服务地址",
    )
    ollama_model: str = Field(
        default="qwen2.5:7b",
        description="Ollama 模型名",
    )

    # === 地理编码（T0.2 使用）===
    gaode_api_key: str = Field(
        default="",
        description="高德地图 API key (https://lbs.amap.com/)，留空则 fallback 到 Nominatim",
    )
    nominatim_user_agent: str = Field(
        default="BJFishing/0.1.0",
        description="Nominatim 请求 User-Agent",
    )

    # === Open-Meteo 气象 API（T0.3 使用）===
    openmeteo_forecast_url: str = Field(
        default="https://api.open-meteo.com/v1/forecast",
        description="Open-Meteo 预报 API endpoint",
    )
    openmeteo_archive_url: str = Field(
        default="https://archive-api.open-meteo.com/v1/archive",
        description="Open-Meteo 历史 API endpoint (ERA5 数据源，1940年至今)",
    )
    openmeteo_daily_quota: int = Field(
        default=10000,
        description="Open-Meteo 日配额（免费 10000 次/天，与 forecast+archive 共享）",
    )
    weather_cache_ttl: int = Field(
        default=3600,
        description="气象数据缓存 TTL（秒，默认 1 小时）",
    )

    # === 数据库（T4.1 使用）===
    feedback_db_path: str = Field(
        default="data/feedback.db",
        description="SQLite 鱼获反馈数据库路径（相对项目根目录）",
    )

    # === 日志（T0.11 使用）===
    log_level: str = Field(
        default="INFO",
        description="日志级别: DEBUG | INFO | WARNING | ERROR",
    )
    log_format: str = Field(
        default="json",
        description="日志格式: json (结构化) | text (可读)",
    )

    # === MCP Server（T0.5 使用）===
    mcp_server_name: str = Field(
        default="bjfishing",
        description="MCP Server 名称",
    )
    mcp_server_version: str = Field(
        default="0.1.0",
        description="MCP Server 版本",
    )


# ============================================================================
# 全局单例 — 所有模块通过 `from config import settings` 使用
# ============================================================================
settings = Settings()

# ============================================================================
# 派生路径常量
# ============================================================================
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
EXAMPLES_DIR = PROJECT_ROOT / "examples"
DATA_DIR = PROJECT_ROOT / "data"

# 确保数据目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
