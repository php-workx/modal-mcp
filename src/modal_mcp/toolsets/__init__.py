"""FastMCP toolset registration for Modal MCP."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from modal_mcp.config import Settings
from modal_mcp.toolsets.apps import register_app_tools
from modal_mcp.toolsets.change import register_change_tools
from modal_mcp.toolsets.discovery import register_discovery_tools
from modal_mcp.toolsets.expert import register_expert_tools


def register_toolsets(mcp: FastMCP[Any], settings: Settings) -> None:
    """Register all v1 toolsets before policy disables unavailable tags."""

    register_discovery_tools(mcp, settings)
    register_app_tools(mcp)
    register_change_tools(mcp)
    register_expert_tools(mcp)


__all__ = ["register_toolsets"]
