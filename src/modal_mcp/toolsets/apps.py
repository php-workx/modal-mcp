"""App and deployment read-only tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import Deployment, Page
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    page_envelope,
    register_read_toolset,
)


def register_app_tools(mcp: FastMCP[Any]) -> None:
    """Register app tools with read-only annotations.

    list/get are handled by register_read_toolset.
    modal_list_app_deployments keeps custom registration: it takes app_ref as
    a required positional string, not the standard optional ref pattern.
    """
    register_read_toolset(
        mcp=mcp,
        entity_name="app",
        list_fn=lambda environment_name=None: get_modal_adapter().list_apps(
            environment_name
        ),
        get_fn=lambda app_ref: get_modal_adapter().get_app(app_ref),
        get_param_name="app_ref",
        not_found_message_template="app not found: {ref}",
        tags={"apps"},
    )

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
