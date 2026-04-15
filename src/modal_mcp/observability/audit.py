"""JSONL audit sink for Modal MCP policy and approval events."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from fastmcp.tools.base import ToolResult

from modal_mcp.config import Settings
from modal_mcp.domain.errors import ModalAdapterError
from modal_mcp.observability.redact import collect_known_secrets, redact_value


class JSONLAuditSink:
    """Append redacted audit events to stdout or a JSONL file."""

    def __init__(
        self,
        target: str | Path | TextIO = "stdout",
        *,
        known_secrets: Iterable[str] = (),
        now: Callable[[], float] | None = None,
    ) -> None:
        self.target = target
        self.known_secrets = frozenset(known_secrets)
        self._now = now or time.time

    def record_decision(self, context: Any, decision: Any) -> None:
        """Record a policy decision for a tool call."""

        self.write_event(
            {
                "type": "policy_decision",
                "tool": decision.tool_name,
                "toolset": decision.toolset,
                "decision": {
                    "allowed": decision.allowed,
                    "code": getattr(decision.code, "value", decision.code),
                    "reason": decision.reason,
                    "policy_version": "v1",
                },
                "mcp_session_id": _session_id(context),
                "metadata": dict(decision.metadata),
            }
        )

    def record_error(self, context: Any, tool_name: str, exc: Exception) -> None:
        """Record a redacted tool error."""

        payload: dict[str, Any] = {
            "type": "tool_error",
            "tool": tool_name,
            "mcp_session_id": _session_id(context),
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
        if isinstance(exc, ModalAdapterError):
            payload["error"]["code"] = exc.code.value
            payload["error"]["details"] = exc.details
        self.write_event(payload)

    def record_result(self, context: Any, tool_name: str, result: ToolResult) -> None:
        """Record a redacted tool result summary."""

        output: dict[str, Any] = {
            "ok": True,
            "structured_content": result.structured_content,
            "meta": result.meta,
        }
        self.write_event(
            {
                "type": "tool_result",
                "tool": tool_name,
                "mcp_session_id": _session_id(context),
                "output": output,
            }
        )

    def record_approval(self, action: str, record: Any) -> None:
        """Record approval-token issuance, approval, or consumption."""

        self.write_event(
            {
                "type": "approval",
                "action": action,
                "token_digest": getattr(record, "token_digest", None),
                "tool": getattr(record, "tool_name", None),
                "actor": getattr(record, "actor", None),
                "auth_session_id": getattr(record, "auth_session_id", None),
                "mcp_session_id": getattr(record, "mcp_session_id", None),
                "workspace": getattr(record, "workspace", None),
                "expires_at": getattr(record, "expires_at", None),
            }
        )

    def write_event(self, event: Mapping[str, Any]) -> None:
        """Write one redacted JSONL audit event."""

        payload = {
            "ts": datetime.fromtimestamp(self._now(), UTC).isoformat(),
            **dict(event),
        }
        redacted = redact_value(payload, known_secrets=self.known_secrets)
        line = json.dumps(redacted, sort_keys=True, separators=(",", ":")) + "\n"
        self._write_line(line)

    def _write_line(self, line: str) -> None:
        if hasattr(self.target, "write"):
            stream = self.target
            assert not isinstance(stream, (str, Path))
            stream.write(line)
            stream.flush()
            return
        if str(self.target) == "stdout":
            sys.stdout.write(line)
            sys.stdout.flush()
            return
        path = Path(self.target).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(line)
            file.flush()
            os.fsync(file.fileno())


def audit_sink_from_settings(settings: Settings) -> JSONLAuditSink:
    """Create the configured audit sink."""

    return JSONLAuditSink(
        settings.modal_mcp_audit_log,
        known_secrets=collect_known_secrets(settings),
    )


def _session_id(context: Any) -> str | None:
    fastmcp_context = getattr(context, "fastmcp_context", None)
    if fastmcp_context is None:
        return None
    try:
        return str(fastmcp_context.session_id)
    except RuntimeError:
        return None


__all__ = ["JSONLAuditSink", "audit_sink_from_settings"]
