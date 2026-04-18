"""Disabled-by-default mutating tool stubs for v1."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.toolsets._common import MUTATING_ANNOTATIONS, disabled_error


def register_change_tools(mcp: FastMCP[Any]) -> None:
    """Register future change tools so policy can hide them by tag."""

    @mcp.tool(name="modal_stop_app", tags={"change"}, annotations=MUTATING_ANNOTATIONS)
    def modal_stop_app(
        app_ref: str,
        dry_run: bool = True,
        approval_token: str | None = None,
    ) -> ToolEnvelope[Any]:
        return _disabled_mutation("modal_stop_app", dry_run, approval_token, app_ref)

    @mcp.tool(
        name="modal_rollback_app",
        tags={"change"},
        annotations=MUTATING_ANNOTATIONS,
    )
    def modal_rollback_app(
        app_ref: str,
        target_version: int | None = None,
        dry_run: bool = True,
        approval_token: str | None = None,
    ) -> ToolEnvelope[Any]:
        return _disabled_mutation(
            "modal_rollback_app",
            dry_run,
            approval_token,
            app_ref,
            target_version=target_version,
        )

    @mcp.tool(
        name="modal_stop_container",
        tags={"change"},
        annotations=MUTATING_ANNOTATIONS,
    )
    def modal_stop_container(
        container_ref: str,
        dry_run: bool = True,
        approval_token: str | None = None,
    ) -> ToolEnvelope[Any]:
        return _disabled_mutation(
            "modal_stop_container",
            dry_run,
            approval_token,
            container_ref,
        )

    @mcp.tool(
        name="modal_terminate_sandbox",
        tags={"change"},
        annotations=MUTATING_ANNOTATIONS,
    )
    def modal_terminate_sandbox(
        sandbox_ref: str,
        dry_run: bool = True,
        approval_token: str | None = None,
    ) -> ToolEnvelope[Any]:
        return _disabled_mutation(
            "modal_terminate_sandbox",
            dry_run,
            approval_token,
            sandbox_ref,
        )


def _disabled_mutation(
    tool_name: str,
    dry_run: bool,
    approval_token: str | None,
    target_ref: str,
    **extra: Any,
) -> ToolEnvelope[Any]:
    del approval_token
    details = {
        "mode": "plan" if dry_run else "disabled",
        "requested_mode": "plan" if dry_run else "execute",
        "plan": {
            "requires_approval": True,
            "approval_token": None,
            "approval_url": None,
            "expires_at": None,
            "impact": f"{tool_name} is specified but disabled in v1",
        },
        "target_ref": target_ref,
        **extra,
    }
    return disabled_error(tool_name, details)


__all__ = ["register_change_tools"]
