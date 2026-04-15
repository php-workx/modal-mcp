"""Unit tests for toolset registration helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from modal_mcp.toolsets.change import register_change_tools
from modal_mcp.toolsets.expert import register_expert_tools


@pytest.mark.asyncio
async def test_change_stubs_return_disabled_capability_errors() -> None:
    """Mutating stubs expose dry_run/approval fields but stay disabled."""

    mcp: FastMCP[None] = FastMCP("test")
    register_change_tools(mcp)

    result = await mcp.call_tool(
        "modal_stop_app",
        {
            "app_ref": "mref1.app",
            "dry_run": True,
            "approval_token": "mappr1.token",
        },
        run_middleware=False,
    )

    payload = result.structured_content
    assert payload is not None
    assert payload["ok"] is False
    assert payload["error"]["code"] == "POLICY_BLOCKED"
    assert payload["error"]["details"]["plan"]["requires_approval"] is True


@pytest.mark.asyncio
async def test_expert_execute_stub_returns_disabled_capability_error() -> None:
    """Expert stubs stay explicit and disabled for v1."""

    mcp: FastMCP[None] = FastMCP("test")
    register_expert_tools(mcp)

    result = await mcp.call_tool(
        "modal_expert_execute",
        {"plan": {"steps": []}, "dry_run": True, "approval_token": "mappr1.token"},
        run_middleware=False,
    )

    payload = result.structured_content
    assert payload is not None
    assert payload["ok"] is False
    assert payload["error"]["code"] == "POLICY_BLOCKED"
    assert payload["error"]["details"]["submitted_plan"] == {"steps": []}
