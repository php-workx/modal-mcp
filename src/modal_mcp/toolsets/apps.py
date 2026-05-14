"""App and deployment read-only tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import App, Deployment, Page
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    envelope,
    not_found,
    page_envelope,
    page_envelope_partial,
)


def register_app_tools(mcp: FastMCP[Any]) -> None:
    """Register app tools with read-only annotations."""

    @mcp.tool(
        name="modal_list_apps",
        tags={"apps"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_apps(environment_name: str | None = None) -> ToolEnvelope[Page[App]]:
        items, warnings = get_modal_adapter().list_apps(environment_name)
        return page_envelope_partial(items, warnings)

    @mcp.tool(
        name="modal_get_app",
        tags={"apps"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_app(
        app_ref: str,
        environment_name: str | None = None,
    ) -> ToolEnvelope[App]:
        app = get_modal_adapter().get_app(app_ref, environment_name)
        if app is None:
            return not_found(f"app not found: {app_ref}")
        return envelope(app)

    @mcp.tool(
        name="modal_list_app_deployments",
        tags={"apps"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_app_deployments(
        app_ref: str,
        environment_name: str | None = None,
    ) -> ToolEnvelope[Page[Deployment]]:
        return page_envelope(
            get_modal_adapter().list_app_deployments(app_ref, environment_name)
        )


__all__ = ["register_app_tools"]
