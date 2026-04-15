"""Container read-only tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastmcp import FastMCP

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import Container, LogsPage, Page
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    envelope,
    not_found,
    page_envelope,
)


def register_container_tools(mcp: FastMCP[Any]) -> None:
    """Register container tools with read-only annotations."""

    @mcp.tool(
        name="modal_list_containers",
        tags={"containers"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_containers(
        environment_name: str | None = None,
        app_ref: str | None = None,
    ) -> ToolEnvelope[Page[Container]]:
        return page_envelope(
            get_modal_adapter().list_containers(environment_name, app_ref)
        )

    @mcp.tool(
        name="modal_get_container",
        tags={"containers"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_container(container_ref: str) -> ToolEnvelope[Container]:
        container = get_modal_adapter().get_container(container_ref)
        if container is None:
            return not_found(f"container not found: {container_ref}")
        return envelope(container)

    @mcp.tool(
        name="modal_get_container_logs",
        tags={"containers", "logs"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_container_logs(
        container_ref: str,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = 200,
        source: str | None = None,
        function_id: str | None = None,
        function_call_id: str | None = None,
        sandbox_id: str | None = None,
        search_text: str | None = None,
    ) -> ToolEnvelope[LogsPage]:
        return envelope(
            get_modal_adapter().get_container_logs(
                container_ref,
                since=since,
                until=until,
                limit=limit,
                source=source,
                function_id=function_id,
                function_call_id=function_call_id,
                sandbox_id=sandbox_id,
                search_text=search_text,
            )
        )


__all__ = ["register_container_tools"]
