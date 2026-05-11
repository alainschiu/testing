import logging
import sys
from pathlib import Path

import structlog

from scout.config import get_settings

_configured = False

_KEY_RX = ("api_key", "anthropic_api_key", "tavily_api_key", "authorization", "secret", "token")


def _redact(_logger, _name, event_dict):
    for k, v in list(event_dict.items()):
        if any(s in k.lower() for s in _KEY_RX) and isinstance(v, str) and v:
            event_dict[k] = v[:4] + "…<redacted>"
    return event_dict


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    s = get_settings()
    s.log_json_path.parent.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, s.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    file_handler = logging.FileHandler(s.log_json_path)
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(file_handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)


def redact_secret(value: str | None) -> str:
    if not value:
        return "<unset>"
    if len(value) < 8:
        return "<set>"
    return f"{value[:4]}…<redacted>"


def log_path() -> Path:
    return get_settings().log_json_path
