"""Discovery and workspace read-only tools."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.config import AuthMode, Settings
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import Environment, Page, Workspace
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    envelope,
    not_found,
    page_envelope,
    page_envelope_partial,
)


class ServerInfo(BaseModel):
    """Fixed-schema model-visible server metadata."""

    model_config = ConfigDict(extra="forbid")

    mode: AuthMode
    read_only: bool
    toolsets: tuple[str, ...]
    version: str
    protocol_version: Literal["2025-06-18"] = "2025-06-18"


def register_discovery_tools(mcp: FastMCP[Any], settings: Settings) -> None:
    """Register discovery tools with read-only annotations.

    All tools use custom registration: modal_list_environments takes no
    environment_name param (environments are global), and modal_discovery_server_info,
    modal_whoami, and modal_list_workspaces also don't follow the list/get pattern.
    """

    @mcp.tool(
        name="modal_discovery_server_info",
        tags={"discovery"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_discovery_server_info() -> ToolEnvelope[ServerInfo]:
        return envelope(
            ServerInfo(
                mode=settings.modal_mcp_auth_mode,
                read_only=settings.modal_mcp_read_only,
                toolsets=tuple(sorted(settings.modal_mcp_enabled_toolsets)),
                version=_package_version(),
            )
        )

    @mcp.tool(
        name="modal_whoami",
        tags={"discovery"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_whoami() -> ToolEnvelope[Workspace]:
        return envelope(get_modal_adapter().whoami())

    @mcp.tool(
        name="modal_list_workspaces",
        tags={"discovery"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_workspaces() -> ToolEnvelope[Page[Workspace]]:
        return page_envelope(get_modal_adapter().list_workspaces())

    @mcp.tool(
        name="modal_list_environments",
        tags={"discovery"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_environments() -> ToolEnvelope[Page[Environment]]:
        items, warnings = get_modal_adapter().list_environments()
        return page_envelope_partial(items, warnings)

    @mcp.tool(
        name="modal_get_environment",
        tags={"discovery"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_environment(environment_name: str) -> ToolEnvelope[Environment]:
        env = get_modal_adapter().get_environment(environment_name)
        if env is None:
            return not_found(f"environment not found: {environment_name}")
        return envelope(env)


def _package_version() -> str:
    try:
        return version("modal-mcp")
    except PackageNotFoundError:
        return "0.1.0"


__all__ = ["ServerInfo", "register_discovery_tools"]
