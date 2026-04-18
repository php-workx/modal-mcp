"""Disabled expert toolset stubs for v1."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.toolsets._common import MUTATING_ANNOTATIONS, disabled_error


def register_expert_tools(mcp: FastMCP[Any]) -> None:
    """Register future expert tools so policy can hide them by tag."""

    @mcp.tool(
        name="modal_expert_search",
        tags={"expert"},
        annotations=MUTATING_ANNOTATIONS,
    )
    def modal_expert_search(query: str) -> ToolEnvelope[Any]:
        return disabled_error(
            "modal_expert_search",
            {
                "query": query,
                "reason": "expert toolset is disabled in v1",
            },
        )

    @mcp.tool(
        name="modal_expert_execute",
        tags={"expert"},
        annotations=MUTATING_ANNOTATIONS,
    )
    def modal_expert_execute(
        plan: dict[str, Any],
        dry_run: bool = True,
        approval_token: str | None = None,
    ) -> ToolEnvelope[Any]:
        del approval_token
        return disabled_error(
            "modal_expert_execute",
            {
                "mode": "plan" if dry_run else "disabled",
                "requested_mode": "plan" if dry_run else "execute",
                "plan": {
                    "requires_approval": True,
                    "approval_token": None,
                    "approval_url": None,
                    "expires_at": None,
                    "impact": "expert execution is specified but disabled in v1",
                },
                "submitted_plan": plan,
            },
        )


__all__ = ["register_expert_tools"]
