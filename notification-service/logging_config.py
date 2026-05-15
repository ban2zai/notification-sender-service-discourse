import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any


_RESERVED_LOG_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}

_REDACTION_PATTERNS = (
    (re.compile(r"/bot\d+:[A-Za-z0-9_-]+"), "/bot<redacted>"),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{25,}\b"), "<telegram-token-redacted>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE), "Bearer <redacted>"),
    (re.compile(r"(apikey['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE), r"\1<redacted>"),
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact(record.getMessage()),
        }

        if record.exc_info:
            payload["exception"] = _redact(self.formatException(record.exc_info))

        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_KEYS and not key.startswith("_"):
                payload[key] = _redact(value)

        return json.dumps(payload, ensure_ascii=False, default=str)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        redacted = value
        for pattern, replacement in _REDACTION_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True

    for logger_name in ("httpx", "httpcore"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.WARNING)
