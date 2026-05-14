"""FastMCP toolset registration for Modal MCP."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from modal_mcp.config import Settings
from modal_mcp.toolsets.apps import register_app_tools
from modal_mcp.toolsets.containers import register_container_tools
from modal_mcp.toolsets.discovery import register_discovery_tools
from modal_mcp.toolsets.expert import register_expert_tools
from modal_mcp.toolsets.logs import register_log_tools
from modal_mcp.toolsets.sandboxes import register_sandbox_tools
from modal_mcp.toolsets.volumes import register_volume_tools


def register_toolsets(mcp: FastMCP[Any], settings: Settings) -> None:
    """Register all v1 toolsets before policy disables unavailable tags."""

    register_discovery_tools(mcp, settings)
    register_app_tools(mcp)
    register_log_tools(mcp)
    register_container_tools(mcp)
    register_volume_tools(mcp)
    register_sandbox_tools(mcp)
    register_expert_tools(mcp)


__all__ = ["register_toolsets"]
