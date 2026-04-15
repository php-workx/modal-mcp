"""Contract tests for generated MCP tool schema snapshots."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_schema_snapshot_is_current() -> None:
    """--check fails when schema/mcp-tools.v1.json is stale."""

    subprocess.run(
        [sys.executable, "scripts/generate_schemas.py", "--check"],
        cwd=ROOT,
        check=True,
    )


def test_schema_snapshot_is_normalized() -> None:
    """Generated schemas strip noisy titles and preserve ToolEnvelope shapes."""

    snapshot = json.loads((ROOT / "schema/mcp-tools.v1.json").read_text())
    assert snapshot["schema_version"] == "v1"
    assert snapshot["compatibility"]["additive_changes"] == "allowed"
    assert all("title" not in json.dumps(tool) for tool in snapshot["tools"])
    tool_names = {tool["name"] for tool in snapshot["tools"]}
    assert "modal_whoami" in tool_names
    assert "modal_stop_app" not in tool_names
    assert any(
        "ToolEnvelope" in json.dumps(tool["output_schema"])
        or "ErrorPayload" in json.dumps(tool["output_schema"])
        for tool in snapshot["tools"]
    )
