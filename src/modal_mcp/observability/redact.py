"""Shared recursive redaction for logs, audit events, and tool results."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping, MutableMapping
from typing import Any

from pydantic import SecretStr

from modal_mcp.config import Settings

REDACTION_PLACEHOLDER = "[REDACTED]"
BASE64_REDACTION_PLACEHOLDER = "[REDACTED_BASE64]"
MIN_SECRET_LENGTH = 4
SHAPE_PATTERNS = (
    re.compile(r"MODAL_TOKEN_[A-Z_]*=[^\s,}\]]+"),
    re.compile(r"\bas-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)
BASE64_LIKE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9+/=_-])([A-Za-z0-9+/=_-]{24,})(?![A-Za-z0-9+/=_-])"
)


def collect_known_secrets(settings: Settings) -> frozenset[str]:
    """Collect configured secret values for exact replacement."""

    values: set[str] = set()
    for field_name in (
        "modal_token_id",
        "modal_token_secret",
        "modal_mcp_signing_keys",
    ):
        value = getattr(settings, field_name)
        if isinstance(value, SecretStr):
            _add_secret(values, value.get_secret_value())
    return frozenset(values)


def redact_value(
    value: Any,
    *,
    known_secrets: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
) -> Any:
    """Recursively redact known secrets and secret-shaped strings."""

    secrets = frozenset(
        secret for secret in known_secrets if len(secret) >= MIN_SECRET_LENGTH
    )
    if isinstance(value, str):
        return redact_string(value, known_secrets=secrets)
    if isinstance(value, Mapping):
        return {
            key: redact_value(item, known_secrets=secrets)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_value(item, known_secrets=secrets) for item in value)
    if isinstance(value, list):
        return [redact_value(item, known_secrets=secrets) for item in value]
    return value


def redact_string(
    value: str,
    *,
    known_secrets: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
) -> str:
    """Redact one string by exact secret values, shape regexes, and base64 scans."""

    redacted = value
    for secret in sorted(known_secrets, key=len, reverse=True):
        if len(secret) >= MIN_SECRET_LENGTH:
            redacted = redacted.replace(secret, REDACTION_PLACEHOLDER)
    for pattern in SHAPE_PATTERNS:
        redacted = pattern.sub(REDACTION_PLACEHOLDER, redacted)
    return BASE64_LIKE_PATTERN.sub(
        lambda match: _redact_base64_match(match.group(1), known_secrets),
        redacted,
    )


def structlog_redact_processor(
    _: Any,
    __: str,
    event_dict: MutableMapping[str, Any],
) -> Mapping[str, Any]:
    """Structlog processor that runs after exception formatting."""

    known = event_dict.pop("_known_secrets", frozenset())
    redacted = redact_value(event_dict, known_secrets=known)
    return dict(redacted)


def _redact_base64_match(
    token: str,
    known_secrets: frozenset[str] | set[str] | tuple[str, ...],
) -> str:
    decoded = _decode_base64_candidate(token)
    if decoded is None:
        return token
    redacted = redact_string(decoded, known_secrets=known_secrets)
    if redacted != decoded:
        return BASE64_REDACTION_PLACEHOLDER
    return token


def _decode_base64_candidate(token: str) -> str | None:
    normalized = token.replace("-", "+").replace("_", "/")
    padding = "=" * (-len(normalized) % 4)
    try:
        decoded = base64.b64decode(normalized + padding, validate=False)
    except (binascii.Error, ValueError):
        return None
    if not decoded:
        return None
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    return text


def _add_secret(values: set[str], value: str | None) -> None:
    if value and len(value) >= MIN_SECRET_LENGTH:
        values.add(value)


__all__ = [
    "BASE64_REDACTION_PLACEHOLDER",
    "REDACTION_PLACEHOLDER",
    "collect_known_secrets",
    "redact_string",
    "redact_value",
    "structlog_redact_processor",
]
