"""Structured JSON logging with credential redaction."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_SECRET_KEYS = re.compile(r"(token|secret|password|authorization|api_key|otp|private)", re.I)
_BOT_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+")


def _redact_value(v: Any) -> Any:
    if isinstance(v, str):
        v = _BOT_TOKEN.sub("bot<redacted>", v)
        v = _BEARER.sub("Bearer <redacted>", v)
        v = re.sub(r"([?&]otp=)[^&\s]+", r"\1<redacted>", v)
    return v


def redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for k in list(event_dict):
        if _SECRET_KEYS.search(k):
            event_dict[k] = "<redacted>"
        else:
            event_dict[k] = _redact_value(event_dict[k])
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        cache_logger_on_first_use=True,
    )
