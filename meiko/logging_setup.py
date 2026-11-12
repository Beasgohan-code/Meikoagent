"""Structured logging shared by bot, API, workers and watchdog."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from meiko.config import get_settings

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("bot_id", "worker_id", "plugin", "chat_id", "request_id", "event"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        reset = "\033[0m" if color else ""
        stamp = datetime.now().strftime("%H:%M:%S")
        extras = " ".join(
            f"{key}={getattr(record, key)}"
            for key in ("bot_id", "worker_id", "plugin", "chat_id")
            if getattr(record, key, None) is not None
        )
        base = f"{color}{stamp} {record.levelname:<7}{reset} {record.name:<28} {record.getMessage()}"
        if extras:
            base = f"{base} [{extras}]"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(service: str = "meiko") -> None:
    global _CONFIGURED
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    if not _CONFIGURED:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(
            JsonFormatter() if settings.log_format == "json" else ConsoleFormatter()
        )
        root.addHandler(stream)
        try:
            settings.ensure_dirs()
            file_handler = logging.FileHandler(
                settings.data_dir / "logs" / f"{service}.log", encoding="utf-8"
            )
            file_handler.setFormatter(JsonFormatter())
            root.addHandler(file_handler)
        except OSError:  # pragma: no cover
            pass
        logging.getLogger("aiogram").setLevel(max(level, logging.WARNING))
        logging.getLogger("sqlalchemy.engine").setLevel(
            logging.INFO if settings.db_echo else logging.WARNING
        )
        _CONFIGURED = True

    logging.getLogger(service).info("%s logging initialised", service)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"meiko.{name}")
