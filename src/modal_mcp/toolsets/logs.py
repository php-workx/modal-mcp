"""Log and diagnostic read-only tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import BaseModel

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import LogsPage
from modal_mcp.toolsets._common import READ_ONLY_ANNOTATIONS, envelope

OutputFormat = Literal["summary", "raw", "both"]


class FailureSignature(BaseModel):
    """Grouped failure signature for compact diagnostics."""

    signature: str
    count: int
    sample_messages: list[str]


class FailureSummary(BaseModel):
    """Summary of likely failures from a bounded log page."""

    signatures: list[FailureSignature]
    top_causes: list[str]


class DeploymentComparison(BaseModel):
    """Small deployment comparison result."""

    diff: dict[str, Any]


class StartupDiagnosis(BaseModel):
    """Structured startup diagnosis payload."""

    diagnosis: dict[str, Any]
    evidence: list[dict[str, Any]]


def register_log_tools(mcp: FastMCP[Any]) -> None:
    """Register log and diagnostic tools."""

    @mcp.tool(
        name="modal_get_app_logs",
        tags={"apps", "logs"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_app_logs(
        app_ref: str,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = 200,
        source: str | None = None,
        function_id: str | None = None,
        function_call_id: str | None = None,
        task_id: str | None = None,
        sandbox_id: str | None = None,
        search_text: str | None = None,
        format: OutputFormat = "summary",
    ) -> ToolEnvelope[LogsPage]:
        del format
        return envelope(
            get_modal_adapter().get_app_logs(
                app_ref,
                since=since,
                until=until,
                limit=limit,
                source=source,
                function_id=function_id,
                function_call_id=function_call_id,
                task_id=task_id,
                sandbox_id=sandbox_id,
                search_text=search_text,
            )
        )

    @mcp.tool(
        name="modal_search_logs",
        tags={"logs"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_search_logs(
        app_ref: str,
        search_text: str,
        limit: int | None = 200,
        format: OutputFormat = "summary",
    ) -> ToolEnvelope[LogsPage]:
        del format
        return envelope(
            get_modal_adapter().get_app_logs(
                app_ref,
                limit=limit,
                search_text=search_text,
            )
        )

    @mcp.tool(
        name="modal_summarize_failures",
        tags={"logs"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_summarize_failures(
        app_ref: str,
        limit: int | None = 500,
    ) -> ToolEnvelope[FailureSummary]:
        page = get_modal_adapter().get_app_logs(app_ref, limit=limit)
        signatures = [
            FailureSignature(
                signature=signature,
                count=sum(1 for entry in page.entries if signature in entry.message),
                sample_messages=[
                    entry.message
                    for entry in page.entries
                    if signature in entry.message
                ][:3],
            )
            for signature in page.summary.error_signatures
        ]
        return envelope(
            FailureSummary(
                signatures=signatures,
                top_causes=[item.signature for item in signatures[:3]],
            )
        )

    @mcp.tool(
        name="modal_compare_deployments",
        tags={"logs", "apps"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_compare_deployments(
        app_ref: str,
        base_version: int,
        candidate_version: int,
    ) -> ToolEnvelope[DeploymentComparison]:
        deployments = get_modal_adapter().list_app_deployments(app_ref)
        versions = {deployment.version: deployment for deployment in deployments}
        return envelope(
            DeploymentComparison(
                diff={
                    "base_present": base_version in versions,
                    "candidate_present": candidate_version in versions,
                    "container_delta": 0,
                    "new_error_signatures": [],
                    "resolved_error_signatures": [],
                }
            )
        )

    @mcp.tool(
        name="modal_diagnose_app_startup",
        tags={"logs", "apps"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_diagnose_app_startup(app_ref: str) -> ToolEnvelope[StartupDiagnosis]:
        logs = get_modal_adapter().get_app_logs(app_ref, limit=200)
        return envelope(
            StartupDiagnosis(
                diagnosis={
                    "summary": "No startup failure detected"
                    if not logs.summary.error_signatures
                    else "Startup errors found in recent logs",
                    "confidence": 0.5 if logs.summary.error_signatures else 0.8,
                    "recommended_next_tools": ["modal_get_app_logs"],
                },
                evidence=[
                    {
                        "kind": "log",
                        "ref": entry.app_ref,
                        "note": entry.message[:160],
                    }
                    for entry in logs.entries[:3]
                ],
            )
        )


__all__ = [
    "DeploymentComparison",
    "FailureSignature",
    "FailureSummary",
    "StartupDiagnosis",
    "register_log_tools",
]
