"""Unit tests for toolset registration helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import FastMCP
from pydantic import SecretStr

from modal_mcp.config import Settings
from modal_mcp.toolsets.change import register_change_tools
from modal_mcp.toolsets.discovery import register_discovery_tools
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


@pytest.mark.asyncio
async def test_modal_discovery_server_info_returns_hosted_read_only_mode(
    tmp_path: Path,
) -> None:
    """Discovery output uses the canonical hosted mode string for hosted auth."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    settings = Settings(
        modal_config_path=modal_config,
        modal_mcp_auth_mode="hosted_oauth",
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_public_origin="https://mcp.example.com",
        modal_mcp_auth_issuer="https://issuer.example.com",
        modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
        modal_mcp_auth_audience="modal-mcp",
        modal_mcp_allowed_redirect_uris=("https://client.example.com/cb",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
    )

    mcp = FastMCP("test")
    register_discovery_tools(mcp, settings)

    result = await mcp.call_tool(
        "modal_discovery_server_info", {}, run_middleware=False
    )

    payload = result.structured_content
    assert payload is not None
    assert payload["ok"] is True
    assert payload["data"]["mode"] == "hosted_read_only_ephemeral"
