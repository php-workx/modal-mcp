#!/usr/bin/env python3
"""Create scrubbed Modal fixture metadata templates."""

from __future__ import annotations

import argparse
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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
    path.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        if "token" in value.lower() or value.startswith("as-"):
            return "[REDACTED]"
        return value
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_redact(item) for item in value]
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
