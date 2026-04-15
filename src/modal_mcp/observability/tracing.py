"""OpenTelemetry middleware and instruments for Modal MCP."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Literal

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from opentelemetry import metrics, trace
from opentelemetry.metrics import Counter, Histogram, ObservableGauge
from opentelemetry.trace import Span

from modal_mcp.config import Settings
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError

MCP_PROTOCOL_VERSION = "2025-06-18"
MODAL_BACKENDS = frozenset({"public_api", "semi_public", "grpc_internal"})
ModalBackend = Literal["public_api", "semi_public", "grpc_internal"]


@dataclass(frozen=True, slots=True)
class ModalMcpInstruments:
    """Metric instruments used by the Modal MCP server."""

    tool_invocations: Counter
    tool_denials: Counter
    adapter_latency_ms: Histogram
    output_bytes: Histogram
    output_truncation_ratio: ObservableGauge
    internal_api_drift: Counter


class OtelMiddleware(Middleware):
    """FastMCP middleware that wraps MCP messages in OpenTelemetry spans."""

    def __init__(
        self,
        settings: Settings,
        *,
        instruments: ModalMcpInstruments | None = None,
        span_factory: Callable[
            [MiddlewareContext[Any], str, Settings | None],
            AbstractContextManager[Span],
        ]
        | None = None,
    ) -> None:
        self.settings = settings
        self.instruments = instruments or create_metric_instruments()
        self._span_factory = span_factory or start_mcp_span

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        method_name = context.method or "unknown"
        tool_name = _tool_name(context)
        attributes = {"mcp.method.name": method_name}
        if tool_name is not None:
            attributes["tool"] = tool_name
        with self._span_factory(context, method_name, self.settings):
            try:
                result = await call_next(context)
            except ModalAdapterError as exc:
                if exc.code is ErrorCode.POLICY_BLOCKED:
                    self.instruments.tool_denials.add(
                        1,
                        {**attributes, "rule": exc.code.value},
                    )
                self.instruments.tool_invocations.add(
                    1,
                    {**attributes, "result": "error"},
                )
                raise
            self.instruments.tool_invocations.add(
                1,
                {**attributes, "result": "ok"},
            )
            return result


def start_mcp_span(
    context: MiddlewareContext[Any],
    method_name: str,
    settings: Settings | None = None,
) -> AbstractContextManager[Span]:
    """Start an MCP span with semantic convention attributes."""

    tracer = trace.get_tracer("modal_mcp")
    attributes: dict[str, str] = {
        "mcp.method.name": method_name,
        "mcp.protocol.version": MCP_PROTOCOL_VERSION,
    }
    session_id = _session_id(context)
    if session_id is not None:
        attributes["mcp.session.id"] = session_id
    if settings is not None and settings.modal_environment is not None:
        attributes["modal.environment"] = settings.modal_environment
    return tracer.start_as_current_span(f"mcp.{method_name}", attributes=attributes)


def start_modal_span(
    op: str,
    backend: ModalBackend,
) -> AbstractContextManager[Span]:
    """Start a Modal adapter span with backend classification."""

    if backend not in MODAL_BACKENDS:
        msg = f"unsupported Modal backend: {backend!r}"
        raise ValueError(msg)
    tracer = trace.get_tracer("modal_mcp")
    return tracer.start_as_current_span(
        f"modal.{op}",
        attributes={
            "modal.backend": backend,
            "modal.operation": op,
        },
    )


def create_metric_instruments() -> ModalMcpInstruments:
    """Create OpenTelemetry metric instruments required by the spec."""

    meter = metrics.get_meter("modal_mcp")
    return ModalMcpInstruments(
        tool_invocations=meter.create_counter(
            "modal_mcp_tool_invocations_total",
            description="Total Modal MCP tool invocations by result.",
        ),
        tool_denials=meter.create_counter(
            "modal_mcp_tool_denials_total",
            description="Total Modal MCP policy denials by rule.",
        ),
        adapter_latency_ms=meter.create_histogram(
            "modal_mcp_adapter_latency_ms",
            unit="ms",
            description="Modal adapter call latency.",
        ),
        output_bytes=meter.create_histogram(
            "modal_mcp_output_bytes",
            unit="By",
            description="Tool output size in bytes.",
        ),
        output_truncation_ratio=meter.create_observable_gauge(
            "modal_mcp_output_truncation_ratio",
            callbacks=[],
            description="Ratio of truncated output bytes to requested bytes.",
        ),
        internal_api_drift=meter.create_counter(
            "modal_mcp_internal_api_drift_total",
            description="Internal Modal API drift events.",
        ),
    )


def _session_id(context: MiddlewareContext[Any]) -> str | None:
    fastmcp_context = context.fastmcp_context
    if fastmcp_context is None:
        return None
    try:
        return str(fastmcp_context.session_id)
    except RuntimeError:
        return None


def _tool_name(context: MiddlewareContext[Any]) -> str | None:
    if context.method != "tools/call":
        return None
    message = context.message
    if isinstance(message, mt.CallToolRequestParams):
        return message.name
    return str(getattr(message, "name", "")) or None


__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MODAL_BACKENDS",
    "ModalBackend",
    "ModalMcpInstruments",
    "OtelMiddleware",
    "create_metric_instruments",
    "start_mcp_span",
    "start_modal_span",
]
