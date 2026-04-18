"""Unit tests for OpenTelemetry tracing helpers."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from fastmcp.server.middleware import MiddlewareContext
from mcp import types as mt
from pydantic import SecretStr

from modal_mcp.config import Settings
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.observability.tracing import (
    MCP_PROTOCOL_VERSION,
    MODAL_BACKENDS,
    ModalMcpInstruments,
    OtelMiddleware,
    create_metric_instruments,
    start_mcp_span,
    start_modal_span,
)


@pytest.fixture
def tracing_settings(tmp_path: Path) -> Settings:
    """Return settings with a Modal environment for span attributes."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    return Settings(
        modal_config_path=modal_config,
        modal_environment="prod",
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "d" * 64),
    )


def test_create_metric_instruments_exposes_required_names() -> None:
    """Metric instruments include every required spec name."""

    instruments = create_metric_instruments()

    assert _instrument_name(instruments.tool_invocations) == (
        "modal_mcp_tool_invocations_total"
    )
    assert _instrument_name(instruments.tool_denials) == "modal_mcp_tool_denials_total"
    assert _instrument_name(instruments.adapter_latency_ms) == (
        "modal_mcp_adapter_latency_ms"
    )
    assert _instrument_name(instruments.output_bytes) == "modal_mcp_output_bytes"
    assert _instrument_name(instruments.output_truncation_ratio) == (
        "modal_mcp_output_truncation_ratio"
    )
    assert _instrument_name(instruments.internal_api_drift) == (
        "modal_mcp_internal_api_drift_total"
    )


def test_start_modal_span_rejects_unknown_backend() -> None:
    """Modal spans are limited to known backend labels."""

    assert frozenset({"public_api", "semi_public", "grpc_internal"}) == MODAL_BACKENDS
    with pytest.raises(ValueError, match="unsupported Modal backend"):
        start_modal_span("AppList", "shell")  # type: ignore[arg-type]


def test_start_mcp_span_sets_required_attributes(
    monkeypatch: pytest.MonkeyPatch,
    tracing_settings: Settings,
) -> None:
    """MCP spans include method, protocol, session, and environment."""

    captured: dict[str, Any] = {}

    class FakeTracer:
        def start_as_current_span(
            self,
            name: str,
            *,
            attributes: dict[str, str],
        ) -> Any:
            captured["name"] = name
            captured["attributes"] = attributes
            return nullcontext()

    monkeypatch.setattr(
        "modal_mcp.observability.tracing.trace.get_tracer",
        lambda _: FakeTracer(),
    )
    context = MiddlewareContext(
        message=object(),
        fastmcp_context=_FakeFastMCPContext("mcp-1"),  # type: ignore[arg-type]
        method="tools/list",
    )

    with start_mcp_span(context, "tools/list", tracing_settings):
        pass

    assert captured["name"] == "mcp.tools/list"
    assert captured["attributes"] == {
        "mcp.method.name": "tools/list",
        "mcp.protocol.version": MCP_PROTOCOL_VERSION,
        "mcp.session.id": "mcp-1",
        "modal.environment": "prod",
    }


@pytest.mark.asyncio
async def test_otel_middleware_counts_success_and_policy_denial(
    tracing_settings: Settings,
) -> None:
    """Middleware counts tool successes and policy denials."""

    instruments = _FakeInstruments()
    middleware = OtelMiddleware(
        tracing_settings,
        instruments=instruments,  # type: ignore[arg-type]
        span_factory=lambda *_: nullcontext(),
    )
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(name="modal_list_apps", arguments={}),
        fastmcp_context=_FakeFastMCPContext("mcp-1"),  # type: ignore[arg-type]
        method="tools/call",
    )

    async def ok_call(_: MiddlewareContext[Any]) -> str:
        return "ok"

    assert await middleware.on_message(context, ok_call) == "ok"
    assert instruments.tool_invocations.calls[-1][1]["result"] == "ok"

    async def denied_call(_: MiddlewareContext[Any]) -> str:
        raise ModalAdapterError(ErrorCode.POLICY_BLOCKED, "blocked")

    with pytest.raises(ModalAdapterError):
        await middleware.on_message(context, denied_call)

    assert instruments.tool_denials.calls[-1][1]["rule"] == "POLICY_BLOCKED"
    assert instruments.tool_invocations.calls[-1][1]["result"] == "error"


class _FakeFastMCPContext:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


def _instrument_name(instrument: Any) -> str:
    name = getattr(instrument, "name", None)
    if name is None:
        name = instrument._name
    return str(name)


class _FakeCounter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[int, dict[str, str]]] = []

    def add(self, value: int, attributes: dict[str, str]) -> None:
        self.calls.append((value, attributes))


class _FakeInstruments(ModalMcpInstruments):
    def __init__(self) -> None:
        counter = _FakeCounter("counter")
        super().__init__(
            tool_invocations=counter,  # type: ignore[arg-type]
            tool_denials=_FakeCounter("denials"),  # type: ignore[arg-type]
            adapter_latency_ms=object(),  # type: ignore[arg-type]
            output_bytes=object(),  # type: ignore[arg-type]
            output_truncation_ratio=object(),  # type: ignore[arg-type]
            internal_api_drift=object(),  # type: ignore[arg-type]
        )
