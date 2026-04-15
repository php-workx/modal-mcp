"""Unit tests for policy and rate-limit primitives."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import types as mt
from pydantic import SecretStr
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from modal_mcp.config import Settings
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.domain.refs import ApprovalPayload, encode_approval
from modal_mcp.policy.approval import (
    APPROVAL_CONFIRMATION_HEADER,
    APPROVAL_CONFIRMATION_VALUE,
    ApprovalActor,
    ApprovalTokenLedger,
    approve_http_request,
)
from modal_mcp.policy.engine import PolicyMiddleware
from modal_mcp.policy.rate_limit import TokenBucketRateLimiter, rate_limit_key
from modal_mcp.policy.rules import (
    CHANGE_TOOLSETS,
    READ_ONLY_TOOLSETS,
    PolicyCode,
    evaluate,
)

_SIGNING_KEYS = "kid1:" + "a" * 64
_SIGNING_KEY_BYTES = bytes.fromhex("a" * 64)


@pytest.fixture
def policy_settings(tmp_path: Path) -> Settings:
    """Return security-valid settings for policy tests."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    return Settings(
        modal_config_path=modal_config,
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_allowed_hosts=("127.0.0.1", "localhost"),
        modal_mcp_signing_keys=SecretStr(_SIGNING_KEYS),
        modal_mcp_read_only=False,
        modal_mcp_enabled_toolsets=READ_ONLY_TOOLSETS | CHANGE_TOOLSETS,
        modal_mcp_mutation_rate_limit_seconds=0,
    )


def _approval_payload(
    *,
    exp: int = 2_000,
    nbf: int = 1_000,
    actor: str = "alice",
    auth_session_id: str = "auth-1",
    mcp_session_id: str = "mcp-1",
    target_refs: tuple[str, ...] = ("mref1.app",),
    remote_mode: str = "self_hosted_byo_token",
) -> ApprovalPayload:
    return ApprovalPayload(
        tool_name="modal_stop_app",
        target_refs=target_refs,
        actor=actor,
        ws="workspace-1",
        mcp_session_id=mcp_session_id,
        auth_session_id=auth_session_id,
        nonce="nonce-1",
        env="prod",
        exp=exp,
        nbf=nbf,
        remote_mode=remote_mode,
    )


def _approval_request(
    *,
    token: str,
    actor: ApprovalActor | None = None,
    authenticated: bool = True,
    mcp_session_id: str = "mcp-1",
    origin: str = "http://127.0.0.1:8765",
    fetch_site: str = "same-origin",
    confirmation: bool = True,
    body: dict[str, Any] | None = None,
) -> Request:
    if authenticated and actor is None:
        actor = ApprovalActor("alice", "auth-1")
    payload = (
        {
            "confirmation": APPROVAL_CONFIRMATION_VALUE,
            "tool_name": "modal_stop_app",
            "workspace": "workspace-1",
            "target_refs": ["mref1.app"],
        }
        if body is None
        else body
    )
    headers = [
        (b"host", b"localhost:8765"),
        (b"origin", origin.encode("latin-1")),
        (b"sec-fetch-site", fetch_site.encode("latin-1")),
        (b"mcp-session-id", mcp_session_id.encode("latin-1")),
        (b"content-type", b"application/json"),
    ]
    if confirmation:
        headers.append(
            (
                APPROVAL_CONFIRMATION_HEADER.encode("latin-1"),
                APPROVAL_CONFIRMATION_VALUE.encode("latin-1"),
            )
        )
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": f"/mcp/approvals/{token}",
        "path_params": {"token": token},
        "headers": headers,
    }
    if actor is not None:
        scope["modal_mcp.actor_context"] = actor

    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {
            "type": "http.request",
            "body": json.dumps(payload).encode("utf-8"),
            "more_body": False,
        }

    return Request(scope, receive)


class _FakeFastMCPContext:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.client_id = "client-1"


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


@pytest.mark.asyncio
async def test_approval_ledger_persists_approval_and_consumption(
    tmp_path: Path,
    policy_settings: Settings,
) -> None:
    """File-backed approvals and consumed-token state survive restart."""

    payload = _approval_payload()
    token = encode_approval(payload, signing_keys=(("kid1", _SIGNING_KEY_BYTES),))
    ledger_path = tmp_path / "approvals.jsonl"
    ledger = ApprovalTokenLedger(ledger_path, now=lambda: 1_005)

    record = await approve_http_request(
        _approval_request(token=token),
        ledger=ledger,
        settings=policy_settings,
        expected_env="prod",
        signing_keys=(("kid1", _SIGNING_KEY_BYTES),),
        now=1_005,
    )

    assert record.status == "approved"
    restarted = ApprovalTokenLedger(ledger_path, now=lambda: 1_006)
    assert restarted.is_approved(token) is True

    await restarted.consume(token, payload, ApprovalActor("alice", "auth-1"))
    consumed_restarted = ApprovalTokenLedger(ledger_path, now=lambda: 1_007)
    assert consumed_restarted.is_consumed(token) is True
    with pytest.raises(ModalAdapterError) as exc_info:
        await consumed_restarted.consume(
            token,
            payload,
            ApprovalActor("alice", "auth-1"),
        )
    assert exc_info.value.code is ErrorCode.POLICY_BLOCKED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_kwargs", "match"),
    [
        ({"authenticated": False}, "authenticated actor"),
        ({"mcp_session_id": "mcp-2"}, "MCP session mismatch"),
        ({"origin": "https://evil.example.com"}, "origin is not allowlisted"),
        ({"fetch_site": "cross-site"}, "cross-site"),
        ({"confirmation": False, "body": {}}, "confirmation"),
    ],
)
async def test_approve_http_request_rejects_invalid_approval_context(
    policy_settings: Settings,
    request_kwargs: dict[str, Any],
    match: str,
) -> None:
    """Approval endpoint checks auth, origin, session, fetch site, and marker."""

    payload = _approval_payload()
    token = encode_approval(payload, signing_keys=(("kid1", _SIGNING_KEY_BYTES),))
    ledger = ApprovalTokenLedger(now=lambda: 1_005)

    with pytest.raises(ModalAdapterError, match=match):
        await approve_http_request(
            _approval_request(token=token, **request_kwargs),
            ledger=ledger,
            settings=policy_settings,
            expected_env="prod",
            signing_keys=(("kid1", _SIGNING_KEY_BYTES),),
            now=1_005,
        )


@pytest.mark.asyncio
async def test_policy_middleware_consumes_approval_strips_token_and_redacts(
    monkeypatch: pytest.MonkeyPatch,
    policy_settings: Settings,
) -> None:
    """Middleware enforces approval before call_next and redacts output."""

    monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", _SIGNING_KEYS)
    payload = _approval_payload()
    token = encode_approval(payload)
    actor = ApprovalActor("alice", "auth-1")
    ledger = ApprovalTokenLedger(now=lambda: 1_006)
    await ledger.approve(token, payload, actor)
    middleware = PolicyMiddleware(
        policy_settings,
        approval_ledger=ledger,
        actor_resolver=lambda _: actor,
        now=lambda: 1_006,
    )
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="modal_stop_app",
            arguments={
                "dry_run": False,
                "approval_token": token,
                "app_ref": "mref1.app",
            },
        ),
        fastmcp_context=_FakeFastMCPContext("mcp-1"),  # type: ignore[arg-type]
        method="tools/call",
    )

    async def call_next(
        next_context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> ToolResult:
        assert "approval_token" not in (next_context.message.arguments or {})
        return ToolResult(
            structured_content={
                "message": "failed with MODAL_TOKEN_SECRET=super-secret"
            }
        )

    result = await middleware.on_call_tool(context, call_next)

    assert ledger.is_consumed(token) is True
    assert result.structured_content == {"message": "failed with [REDACTED]"}


@pytest.mark.asyncio
async def test_policy_middleware_blocks_unapproved_mutation(
    monkeypatch: pytest.MonkeyPatch,
    policy_settings: Settings,
) -> None:
    """Mutating dry_run=false calls require prior out-of-band approval."""

    monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", _SIGNING_KEYS)
    payload = _approval_payload()
    token = encode_approval(payload)
    actor = ApprovalActor("alice", "auth-1")
    middleware = PolicyMiddleware(
        policy_settings,
        approval_ledger=ApprovalTokenLedger(now=lambda: 1_006),
        actor_resolver=lambda _: actor,
        now=lambda: 1_006,
    )
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="modal_stop_app",
            arguments={
                "dry_run": False,
                "approval_token": token,
                "app_ref": "mref1.app",
            },
        ),
        fastmcp_context=_FakeFastMCPContext("mcp-1"),  # type: ignore[arg-type]
        method="tools/call",
    )

    async def call_next(_: MiddlewareContext[mt.CallToolRequestParams]) -> ToolResult:
        raise AssertionError("call_next must not run for unapproved mutations")

    with pytest.raises(ModalAdapterError) as exc_info:
        await middleware.on_call_tool(context, call_next)

    assert exc_info.value.code is ErrorCode.POLICY_BLOCKED
