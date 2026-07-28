"""logging.py — 结构化日志（JSON formatter，可配置 level）.

T0.11: 提供统一的日志配置入口.
- JSON 格式: 结构化字段（timestamp/level/logger/message/extra）
- Text 格式: 可读的传统格式
- 通过 config.settings.log_level / log_format 控制

Usage:
    from logging_config import setup_logging
    setup_logging()  # 在程序入口调用一次
    import logging
    logger = logging.getLogger(__name__)
    logger.info("message", extra={"spot": "密云水库"})
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Any

from config import BJ_TZ, settings

# 标准日志字段（不放入 extra）
_RESERVED: frozenset[str] = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


class JsonFormatter(logging.Formatter):
    """JSON 结构化日志 formatter.

    输出格式:
        {"timestamp": "2026-07-28T11:00:00+08:00", "level": "INFO",
         "logger": "services.weather", "message": "...", "extra": {...}}
    """

    def format(self, record: logging.LogRecord) -> str:
        # 时间戳（BJ_TZ ISO 格式）
        ts = datetime.fromtimestamp(record.created, tz=BJ_TZ).isoformat()

        # 基础字段
        entry: dict[str, Any] = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 异常信息
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        # 额外字段（extra=... 传入的）
        extra: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value, ensure_ascii=False)
                    extra[key] = value
                except (TypeError, ValueError):
                    extra[key] = repr(value)
        if extra:
            entry["extra"] = extra

        return json.dumps(entry, ensure_ascii=False)


def _make_text_formatter() -> logging.Formatter:
    """传统可读 formatter."""
    return logging.Formatter(
        fmt="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging() -> None:
    """配置根 logger — 根据 settings.log_level / log_format.

    在程序入口（cli.py / server.py）调用一次.
    后续模块只需: import logging; logger = logging.getLogger(__name__)
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    if settings.log_format.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(_make_text_formatter())

    root = logging.getLogger()
    root.setLevel(level)
    # 清除已有 handler（避免重复）
    root.handlers.clear()
    root.addHandler(handler)

    # 第三方库降噪
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取 logger（便捷函数，等价于 logging.getLogger）."""
    return logging.getLogger(name)
