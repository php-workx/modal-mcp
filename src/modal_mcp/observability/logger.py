"""Structlog configuration for Modal MCP."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any

import structlog

from modal_mcp.config import Settings
from modal_mcp.observability.redact import (
    collect_known_secrets,
    structlog_redact_processor,
)


def configure_logging(
    settings: Settings | None = None,
    *,
    known_secrets: Iterable[str] = (),
) -> None:
    """Configure structlog with redaction after exception formatting."""

    secrets = set(known_secrets)
    if settings is not None:
        secrets.update(collect_known_secrets(settings))

    def inject_known_secrets(
        _: Any,
        __: str,
        event_dict: MutableMapping[str, Any],
    ) -> Mapping[str, Any]:
        event_dict["_known_secrets"] = frozenset(secrets)
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            inject_known_secrets,
            structlog_redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "modal_mcp") -> Any:
    """Return the configured Modal MCP logger."""

    return structlog.get_logger(name)


__all__ = ["configure_logging", "get_logger"]
