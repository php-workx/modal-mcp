"""Contract tests for generated MCP tool schema snapshots."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _contains_key(node: object, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(value, key) for value in node)
    return False


def _contains_ref(node: object, names: tuple[str, ...]) -> bool:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and any(name in ref for name in names):
            return True
        return any(_contains_ref(value, names) for value in node.values())
    if isinstance(node, list):
        return any(_contains_ref(value, names) for value in node)
    return False


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
    assert all(not _contains_key(tool, "title") for tool in snapshot["tools"])
    tool_names = {tool["name"] for tool in snapshot["tools"]}
    assert "modal_whoami" in tool_names
    assert "modal_stop_app" not in tool_names
    assert any(
        _contains_ref(tool["output_schema"], ("ToolEnvelope", "ErrorPayload"))
        for tool in snapshot["tools"]
    )
