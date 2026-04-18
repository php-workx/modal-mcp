#!/usr/bin/env python3
"""Create scrubbed Modal fixture metadata templates."""

from __future__ import annotations

import argparse
import base64
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SECRET_KEY_PARTS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "password",
        "secret",
        "token",
    }
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bbearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bas-[A-Za-z0-9_-]{8,64}\b"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{1,128}\.[A-Za-z0-9_-]{1,128}\.[A-Za-z0-9_-]{1,128}\b"
    ),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
)


def build_fixture(
    *,
    rpc: str,
    source_modal_version: str,
    request_metadata: dict[str, Any],
    response_payload: dict[str, Any],
    protobuf_wire_bytes: bytes = b"",
) -> dict[str, Any]:
    """Build deterministic, redacted fixture metadata."""

    return {
        "source_modal_version": source_modal_version,
        "captured_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "rpc": rpc,
        "request_metadata": _redact(request_metadata),
        "protobuf_wire_bytes_b64": base64.b64encode(protobuf_wire_bytes).decode(
            "ascii"
        ),
        "response_payload": _redact(response_payload),
    }


def write_fixture(path: Path, fixture: dict[str, Any]) -> None:
    """Write one deterministic JSON fixture."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(fixture, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _redact(value: Any, *, key: str | None = None) -> Any:
    lower_key = (key or "").lower()
    if lower_key and any(part in lower_key for part in SECRET_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, str):
        lower_value = value.lower()
        if any(part in lower_value for part in SECRET_KEY_PARTS):
            return "[REDACTED]"
        if any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
            return "[REDACTED]"
        return value
    if isinstance(value, dict):
        return {
            dict_key: _redact(item, key=dict_key)
            for dict_key, item in sorted(value.items())
        }
    if isinstance(value, list):
        return [_redact(item, key=key) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", required=True)
    parser.add_argument("--modal-version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    fixture = build_fixture(
        rpc=args.rpc,
        source_modal_version=args.modal_version,
        request_metadata={"capture_mode": "template"},
        response_payload={"items": []},
    )
    write_fixture(args.output, fixture)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
