"""Audit-sink protocol for policy decisions and tool outcomes.

The protocol is intentionally narrow — three methods, each accepting
positional context plus event-specific arguments — so multiple concrete
implementations (NullAuditSink for tests, JSONLAuditSink for production)
can satisfy it without inheritance.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from fastmcp.tools.base import ToolResult


@runtime_checkable
class AuditSink(Protocol):
    """Receive policy decisions and tool outcomes for redacted audit logging."""

    def record_decision(self, context: Any, decision: Any) -> None:
        """Record a policy decision for a tool call."""

    def record_error(self, context: Any, tool_name: str, exc: Exception) -> None:
        """Record a redacted tool error."""

    def record_result(
        self, context: Any, tool_name: str, result: ToolResult
    ) -> None:
        """Record a redacted tool result summary."""


class NullAuditSink:
    """No-op audit hook used when no structured audit sink is configured."""

    def record_decision(self, *_: Any, **__: Any) -> None:
        return

    def record_error(self, *_: Any, **__: Any) -> None:
        return

    def record_result(self, *_: Any, **__: Any) -> None:
        return


__all__ = ["AuditSink", "NullAuditSink"]
