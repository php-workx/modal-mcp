"""Unit tests for policy and rate-limit primitives."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from modal_mcp.policy.rate_limit import TokenBucketRateLimiter, rate_limit_key
from modal_mcp.policy.rules import (
    CHANGE_TOOLSETS,
    READ_ONLY_TOOLSETS,
    PolicyCode,
    evaluate,
)


def test_policy_allows_known_read_only_toolset() -> None:
    """Known read-only toolsets are allowed when enabled."""

    decision = evaluate(
        tool_name="list_apps",
        toolset="apps",
        read_only=True,
        enabled_toolsets=READ_ONLY_TOOLSETS,
        metadata={"method": "tools/call"},
    )

    assert decision.allowed is True
    assert decision.code is PolicyCode.ALLOWED
    assert decision.metadata["method"] == "tools/call"


def test_policy_denies_unknown_or_disabled_toolsets() -> None:
    """Policy defaults deny unknown and disabled toolsets."""

    unknown = evaluate(tool_name="surprise", toolset="unknown")
    disabled = evaluate(
        tool_name="list_apps",
        toolset="apps",
        enabled_toolsets={"discovery"},
    )

    assert unknown.allowed is False
    assert unknown.code is PolicyCode.UNKNOWN_TOOL
    assert disabled.allowed is False
    assert disabled.code is PolicyCode.TOOLSET_DISABLED


def test_read_only_blocks_change_and_expert_toolsets() -> None:
    """Read-only mode blocks mutating toolsets even when they are enabled."""

    for toolset in CHANGE_TOOLSETS:
        decision = evaluate(
            tool_name=f"{toolset}_operation",
            toolset=toolset,
            read_only=True,
            enabled_toolsets=READ_ONLY_TOOLSETS | CHANGE_TOOLSETS,
        )
        assert decision.allowed is False
        assert decision.code is PolicyCode.READ_ONLY_BLOCKED


def test_policy_can_allow_change_toolsets_when_not_read_only() -> None:
    """Future mutating modes can opt into change/expert explicitly."""

    decision = evaluate(
        tool_name="stop_app",
        toolset="change",
        read_only=False,
        enabled_toolsets=READ_ONLY_TOOLSETS | CHANGE_TOOLSETS,
    )

    assert decision.allowed is True


def test_rate_limit_key_hierarchy_ignores_mcp_session_id() -> None:
    """Rate-limit keys follow auth -> actor -> remote -> global hierarchy."""

    assert (
        rate_limit_key(
            auth_session_id="auth",
            actor_principal="actor",
            remote_address="127.0.0.1",
            mcp_session_id="client-controlled",
        )
        == "auth_session:auth"
    )
    assert (
        rate_limit_key(actor_principal="actor", mcp_session_id="client-controlled")
        == "actor:actor"
    )
    assert (
        rate_limit_key(remote_address="127.0.0.1", mcp_session_id="client-controlled")
        == "remote:127.0.0.1"
    )
    assert rate_limit_key(mcp_session_id="client-controlled") == "global"


def test_initialize_without_identity_is_remote_address_capped() -> None:
    """Initialize uses remote address when no authenticated identity exists."""

    assert (
        rate_limit_key(method="initialize", remote_address="203.0.113.10")
        == "remote:203.0.113.10"
    )


def test_token_bucket_rate_limiter_consumes_and_refills() -> None:
    """Token buckets consume capacity and refill over deterministic time."""

    now = 0.0

    def clock() -> float:
        return now

    limiter = TokenBucketRateLimiter(
        capacity=2.0,
        refill_rate_per_second=1.0,
        now=clock,
    )

    assert limiter.allow("actor") is True
    assert limiter.allow("actor") is True
    assert limiter.allow("actor") is False

    now = 1.5

    assert limiter.remaining("actor") == pytest.approx(1.5)
    assert limiter.allow("actor") is True
    assert limiter.remaining("actor") == pytest.approx(0.5)
