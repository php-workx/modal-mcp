"""Container read-only tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastmcp import FastMCP

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope, ok
from modal_mcp.domain.models import Container, LogsPage, Page
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    REQUEST_ID,
    envelope,
    not_found,
    page_envelope_partial,
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
        items, warnings = get_modal_adapter().list_containers(environment_name, app_ref)
        return page_envelope_partial(items, warnings)

    @mcp.tool(
        name="modal_get_container",
        tags={"containers"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_container(task_id: str) -> ToolEnvelope[Container]:
        container = get_modal_adapter().get_container(task_id)
        if container is None:
            return not_found(f"container not found: {task_id}")
        return envelope(container)

    @mcp.tool(
        name="modal_get_container_logs",
        tags={"containers", "logs"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_container_logs(
        task_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = 200,
        source: str | None = None,
        function_id: str | None = None,
        function_call_id: str | None = None,
        sandbox_id: str | None = None,
        search_text: str | None = None,
    ) -> ToolEnvelope[LogsPage]:
        result = get_modal_adapter().get_container_logs(
            task_id,
            since=since,
            until=until,
            limit=limit,
            source=source,
            function_id=function_id,
            function_call_id=function_call_id,
            sandbox_id=sandbox_id,
            search_text=search_text,
        )
        warnings: list[str] = []
        if not result.entries:
            warnings.append(
                f"Zero log entries returned for task_id={task_id!r}. "
                "Possible reasons: (1) container has expired, "
                "(2) time range did not match activity window, "
                "(3) logs not yet ingested. "
                "Try modal_get_app_logs with app_ref and task_id filter for a broader search."  # noqa: E501
            )
        return ok(result, request_id=REQUEST_ID, warnings=warnings)


__all__ = ["register_container_tools"]
