"""Unit tests for PolicyContext and AuditSink protocol."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from modal_mcp.config import ConfigError, Settings
from modal_mcp.policy.approval import ApprovalTokenLedger
from modal_mcp.policy.audit import AuditSink, NullAuditSink
from modal_mcp.policy.context import PolicyContext
from modal_mcp.policy.rate_limit import TokenBucketRateLimiter
from modal_mcp.policy.rules import CHANGE_TOOLSETS, READ_ONLY_TOOLSETS

_SIGNING_KEYS = "kid1:" + "a" * 64


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    base: dict[str, Any] = {
        "modal_config_path": modal_config,
        "modal_mcp_allowed_origins": ("http://127.0.0.1:8765",),
        "modal_mcp_allowed_hosts": ("127.0.0.1", "localhost"),
        "modal_mcp_signing_keys": SecretStr(_SIGNING_KEYS),
        "modal_mcp_read_only": True,
        "modal_mcp_enabled_toolsets": READ_ONLY_TOOLSETS | CHANGE_TOOLSETS,
        "modal_mcp_mutation_rate_limit_seconds": 0,
    }
    base.update(overrides)
    return Settings(**base)


class TestPolicyContextFromSettings:
    def test_returns_policy_context_instance(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        assert isinstance(ctx, PolicyContext)

    def test_binds_approval_ledger(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        assert isinstance(ctx.approval_ledger, ApprovalTokenLedger)

    def test_binds_rate_limiter(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        assert isinstance(ctx.rate_limiter, TokenBucketRateLimiter)

    def test_mutation_limiter_none_when_disabled(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(
            _settings(tmp_path, modal_mcp_mutation_rate_limit_seconds=0)
        )
        assert ctx.mutation_limiter is None

    def test_mutation_limiter_present_when_configured(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(
            _settings(tmp_path, modal_mcp_mutation_rate_limit_seconds=5)
        )
        assert isinstance(ctx.mutation_limiter, TokenBucketRateLimiter)

    def test_signing_keys_parsed_into_tuple(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        assert isinstance(ctx.signing_keys, tuple)
        assert len(ctx.signing_keys) >= 1
        kid, key = ctx.signing_keys[0]
        assert kid == "kid1"
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_now_is_callable_returning_int(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        now_value = ctx.now()
        assert isinstance(now_value, int)
        # within 5 seconds of wall clock
        assert abs(now_value - int(time.time())) < 5

    def test_audit_sink_implements_protocol(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        # Either NullAuditSink or JSONLAuditSink — both satisfy AuditSink.
        assert hasattr(ctx.audit_sink, "record_decision")
        assert hasattr(ctx.audit_sink, "record_error")
        assert hasattr(ctx.audit_sink, "record_result")

    def test_actor_resolver_is_callable(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        assert callable(ctx.actor_resolver)


class TestPolicyContextIsFrozen:
    def test_cannot_reassign_field(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        with pytest.raises((AttributeError, TypeError)):
            ctx.approval_ledger = ApprovalTokenLedger(None)  # type: ignore[misc]


class TestPolicyContextStartupInvariants:
    """Misconfiguration must fail at PolicyContext.from_settings.

    Not on the first tool call — the whole point of the dataclass is to
    surface bad config at startup with a clean traceback.
    """

    def test_raises_when_signing_keys_missing_and_not_read_only(
        self, tmp_path: Path
    ) -> None:
        # Construct Settings that pass startup validation, then mutate
        # signing_keys to None to simulate the deletion path.
        settings = _settings(tmp_path, modal_mcp_read_only=False)
        # Bypass model_validator post-construction: object.__setattr__ on
        # frozen=False BaseSettings is permitted.
        object.__setattr__(settings, "modal_mcp_signing_keys", None)

        with pytest.raises(ConfigError, match="signing keys"):
            PolicyContext.from_settings(settings)

    def test_allows_missing_signing_keys_when_read_only(self, tmp_path: Path) -> None:
        """Read-only mode never consumes approvals.

        Empty signing keys are tolerable when MODAL_MCP_READ_ONLY=true.
        """

        settings = _settings(tmp_path, modal_mcp_read_only=True)
        object.__setattr__(settings, "modal_mcp_signing_keys", None)
        ctx = PolicyContext.from_settings(settings)
        assert ctx.signing_keys == ()


class TestNullAuditSink:
    def test_record_decision_is_noop(self) -> None:
        sink = NullAuditSink()
        sink.record_decision(object(), object())  # no exception

    def test_record_error_is_noop(self) -> None:
        sink = NullAuditSink()
        sink.record_error(object(), "tool", RuntimeError("boom"))

    def test_record_result_is_noop(self) -> None:
        sink = NullAuditSink()
        sink.record_result(object(), "tool", object())

    def test_satisfies_protocol_at_runtime(self) -> None:
        sink: AuditSink = NullAuditSink()  # static check via assignment
        assert sink is not None
