"""Integration tests for audit JSONL observability."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from modal_mcp.config import Settings
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.observability.audit import JSONLAuditSink, audit_sink_from_settings
from modal_mcp.policy.rules import evaluate


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


class _FakeFastMCPContext:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _FakeContext:
    def __init__(self, session_id: str) -> None:
        self.fastmcp_context = _FakeFastMCPContext(session_id)
