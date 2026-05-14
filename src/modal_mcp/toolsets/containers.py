"""Container read-only tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastmcp import FastMCP

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope, ok
from modal_mcp.domain.models import LogsPage
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    REQUEST_ID,
    register_read_toolset,
)


def register_container_tools(mcp: FastMCP[Any]) -> None:
    """Register container tools with read-only annotations.

    list/get are handled by register_read_toolset.
    modal_get_container_logs keeps custom registration: unique time-range
    params, source/filter params, and an empty-log hint that references the
    task_id in a formatted warning message.
    """
    register_read_toolset(
        mcp=mcp,
        entity_name="container",
        list_fn=lambda environment_name=None, app_ref=None: (
            get_modal_adapter().list_containers(environment_name, app_ref)
        ),
        get_fn=lambda task_id: get_modal_adapter().get_container(task_id),
        get_param_name="task_id",
        not_found_message_template="container not found: {ref}",
        tags={"containers"},
        extra_list_params=["app_ref"],
    )

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
