"""Sandbox read-only tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import Page, SandboxSummary
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    envelope,
    not_found,
    page_envelope,
)


class SandboxStdio(BaseModel):
    """Bounded sandbox stdio payload."""

    stdout: str
    stderr: str
    truncated: bool


def register_sandbox_tools(mcp: FastMCP[Any]) -> None:
    """Register sandbox tools with read-only annotations."""

    @mcp.tool(
        name="modal_list_sandboxes",
        tags={"sandboxes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_sandboxes(
        environment_name: str | None = None,
        app_ref: str | None = None,
        include_finished: bool = False,
    ) -> ToolEnvelope[Page[SandboxSummary]]:
        return page_envelope(
            get_modal_adapter().list_sandboxes(
                environment_name,
                app_ref,
                include_finished=include_finished,
            )
        )

    @mcp.tool(
        name="modal_get_sandbox",
        tags={"sandboxes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_sandbox(sandbox_ref: str) -> ToolEnvelope[SandboxSummary]:
        sandbox = get_modal_adapter().get_sandbox(sandbox_ref)
        if sandbox is None:
            return not_found(f"sandbox not found: {sandbox_ref}")
        return envelope(sandbox)

    @mcp.tool(
        name="modal_get_sandbox_stdio",
        tags={"sandboxes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_sandbox_stdio(
        sandbox_ref: str,
        tail_bytes: Annotated[int, Field(ge=1, le=65_536)] = 8_192,
    ) -> ToolEnvelope[SandboxStdio]:
        stdout, stderr = get_modal_adapter().get_sandbox_stdio(sandbox_ref)
        stdout_bytes = stdout.encode("utf-8")
        stderr_bytes = stderr.encode("utf-8")
        stdout_truncated = len(stdout_bytes) > tail_bytes
        stderr_truncated = len(stderr_bytes) > tail_bytes
        if stdout_truncated:
            stdout = stdout_bytes[-tail_bytes:].decode("utf-8", errors="replace")
        if stderr_truncated:
            stderr = stderr_bytes[-tail_bytes:].decode("utf-8", errors="replace")
        truncated = stdout_truncated or stderr_truncated
        return envelope(SandboxStdio(stdout=stdout, stderr=stderr, truncated=truncated))


__all__ = ["SandboxStdio", "register_sandbox_tools"]
