#!/usr/bin/env python3
"""Generate deterministic MCP tool schema snapshots."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from modal_mcp.config import Settings  # noqa: E402
from modal_mcp.server import create_mcp  # noqa: E402

SNAPSHOT = ROOT / "schema" / "mcp-tools.v1.json"


def normalize_schema(value: Any) -> Any:
    """Normalize FastMCP/Pydantic output_schema noise for stable snapshots."""

    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if key == "title":
                continue
            normalized[key] = normalize_schema(value[key])
        if "$defs" in normalized:
            normalized["$defs"] = {
                key: normalized["$defs"][key] for key in sorted(normalized["$defs"])
            }
        return normalized
    if isinstance(value, list):
        return [normalize_schema(item) for item in value]
    return value


async def generate_snapshot() -> dict[str, Any]:
    """Collect FastMCP tool descriptors from create_mcp()."""

    with tempfile.TemporaryDirectory() as tmp:
        modal_config = Path(tmp) / "modal.toml"
        modal_config.write_text("[default]\n", encoding="utf-8")
        settings = Settings(
            modal_config_path=modal_config,
            modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
            modal_mcp_signing_keys=SecretStr("kid1:" + "f" * 64),
        )
        mcp = create_mcp(settings)
        tools = await mcp.list_tools(run_middleware=False)

    return {
        "schema_version": "v1",
        "compatibility": {
            "additive_changes": "allowed",
            "renames_type_changes_removals": "require v2 and retain v1 for one release",
        },
        "tools": [
            normalize_schema(
                tool.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude={"fn", "return_type", "auth"},
                )
            )
            for tool in sorted(tools, key=lambda item: item.name)
        ],
    }


def _render(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="fail if snapshot is stale"
    )
    args = parser.parse_args()

    rendered = _render(asyncio.run(generate_snapshot()))
    if args.check:
        current = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
        if current != rendered:
            print("schema/mcp-tools.v1.json is stale", file=sys.stderr)
            return 1
        return 0

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
