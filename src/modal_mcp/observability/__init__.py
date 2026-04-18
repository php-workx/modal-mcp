"""Observability helpers for Modal MCP."""

from modal_mcp.observability.audit import JSONLAuditSink, audit_sink_from_settings
from modal_mcp.observability.logger import configure_logging, get_logger
from modal_mcp.observability.redact import (
    REDACTION_PLACEHOLDER,
    collect_known_secrets,
    redact_value,
    structlog_redact_processor,
)
from modal_mcp.observability.tracing import (
    MCP_PROTOCOL_VERSION,
    MODAL_BACKENDS,
    ModalMcpInstruments,
    OtelMiddleware,
    create_metric_instruments,
    start_mcp_span,
    start_modal_span,
)

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MODAL_BACKENDS",
    "REDACTION_PLACEHOLDER",
    "JSONLAuditSink",
    "ModalMcpInstruments",
    "OtelMiddleware",
    "audit_sink_from_settings",
    "collect_known_secrets",
    "configure_logging",
    "create_metric_instruments",
    "get_logger",
    "redact_value",
    "start_mcp_span",
    "start_modal_span",
    "structlog_redact_processor",
]
