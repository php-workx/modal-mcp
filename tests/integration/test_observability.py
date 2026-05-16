"""Integration tests for audit JSONL observability."""

from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import pytest
from fastmcp.server.middleware import MiddlewareContext
from mcp import types as mt
from pydantic import SecretStr

from modal_mcp.config import Settings
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.observability.audit import JSONLAuditSink, audit_sink_from_settings
from modal_mcp.observability.tracing import (
    MCP_PROTOCOL_VERSION,
    OtelMiddleware,
    start_modal_span,
)
from modal_mcp.policy.rules import evaluate
from modal_mcp.server import create_mcp


def test_audit_sink_writes_redacted_jsonl(tmp_path: Path) -> None:
    """Audit JSONL records policy decisions with redacted nested output."""

    audit_path = tmp_path / "audit.jsonl"
    sink = JSONLAuditSink(
        audit_path,
        known_secrets=("token-secret-value",),
        now=lambda: 1_900_000_000,
    )
    decision = evaluate(
        tool_name="modal_list_apps",
        toolset="apps",
        metadata={"preview": {"token": "token-secret-value"}},
    )

    sink.record_decision(_FakeContext("mcp-1"), decision)
    sink.record_error(
        _FakeContext("mcp-1"),
        "modal_list_apps",
        ModalAdapterError(
            ErrorCode.UPSTREAM_ERROR,
            "failure token-secret-value MODAL_TOKEN_SECRET=plain-text",
        ),
    )

    lines = [json.loads(line) for line in audit_path.read_text().splitlines()]

    assert lines[0]["type"] == "policy_decision"
    assert lines[0]["metadata"]["preview"]["token"] == "[REDACTED]"
    assert lines[1]["error"]["message"] == "failure [REDACTED] [REDACTED]"


def test_audit_sink_from_settings_collects_configured_secrets(tmp_path: Path) -> None:
    """Settings-backed audit sink redacts loaded Modal credentials."""

    modal_config = tmp_path / "modal.toml"
    audit_path = tmp_path / "audit.jsonl"
    modal_config.write_text("[default]\n", encoding="utf-8")
    settings = Settings(
        modal_config_path=modal_config,
        modal_token_id=SecretStr("token-id-secret"),
        modal_token_secret=SecretStr("token-secret-value"),
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "c" * 64),
        modal_mcp_audit_log=str(audit_path),
    )

    sink = audit_sink_from_settings(settings)
    sink.write_event({"event": "token-secret-value"})

    line = json.loads(audit_path.read_text())
    assert line["event"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_otel_mcp_span_parents_modal_span_and_records_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """MCP spans carry semantic attrs and parent Modal adapter spans."""

    settings = _settings(tmp_path)
    spans: list[_RecordedSpan] = []
    current_span: ContextVar[_RecordedSpan | None] = ContextVar(
        "current_span",
        default=None,
    )
    monkeypatch.setattr(
        "modal_mcp.observability.tracing.trace.get_tracer",
        lambda _: _RecordingTracer(spans, current_span),
    )
    middleware = OtelMiddleware(settings)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(name="modal_list_apps", arguments={}),
        fastmcp_context=_FakeFastMCPContext("mcp-1"),  # type: ignore[arg-type]
        method="tools/call",
    )

    async def call_next(_: MiddlewareContext[Any]) -> str:
        with start_modal_span("AppList", "grpc_internal"):
            return "ok"

    assert await middleware.on_message(context, call_next) == "ok"

    assert [span.name for span in spans] == ["mcp.tools/call", "modal.AppList"]
    assert spans[1].parent is spans[0]
    assert spans[0].attributes["mcp.method.name"] == "tools/call"
    assert spans[0].attributes["mcp.session.id"] == "mcp-1"
    assert spans[0].attributes["mcp.protocol.version"] == MCP_PROTOCOL_VERSION
    assert spans[0].attributes["modal.environment"] == "prod"
    assert spans[1].attributes["modal.backend"] == "grpc_internal"


def test_create_mcp_registers_otel_middleware(tmp_path: Path) -> None:
    """Server stack includes OpenTelemetry middleware before policy handling."""

    mcp = create_mcp(_settings(tmp_path))

    assert any(
        type(middleware).__name__ == "OtelMiddleware" for middleware in mcp.middleware
    )


class _FakeFastMCPContext:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _FakeContext:
    def __init__(self, session_id: str) -> None:
        self.fastmcp_context = _FakeFastMCPContext(session_id)


class _RecordedSpan:
    def __init__(
        self,
        name: str,
        attributes: dict[str, str],
        parent: _RecordedSpan | None,
    ) -> None:
        self.name = name
        self.attributes = attributes
        self.parent = parent


class _RecordingTracer:
    def __init__(
        self,
        spans: list[_RecordedSpan],
        current_span: ContextVar[_RecordedSpan | None],
    ) -> None:
        self.spans = spans
        self.current_span = current_span

    def start_as_current_span(
        self,
        name: str,
        *,
        attributes: dict[str, str],
    ) -> _SpanContext:
        span = _RecordedSpan(name, attributes, self.current_span.get())
        self.spans.append(span)
        return _SpanContext(self.current_span, span)


class _SpanContext:
    def __init__(
        self,
        current_span: ContextVar[_RecordedSpan | None],
        span: _RecordedSpan,
    ) -> None:
        self.current_span = current_span
        self.span = span
        self.token: object | None = None

    def __enter__(self) -> _RecordedSpan:
        self.token = self.current_span.set(self.span)
        return self.span

    def __exit__(self, *_: object) -> None:
        if self.token is not None:
            self.current_span.reset(self.token)  # type: ignore[arg-type]


def _settings(tmp_path: Path) -> Settings:
    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    return Settings(
        modal_config_path=modal_config,
        modal_token_id=SecretStr("ak-id"),
        modal_token_secret=SecretStr("ak-secret"),
        modal_environment="prod",
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "e" * 64),
    )
