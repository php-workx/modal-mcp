"""FastMCP middleware for Modal MCP policy enforcement."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import mcp.types as mt
from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from pydantic import BaseModel, ConfigDict, TypeAdapter

from modal_mcp.config import Settings
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.domain.refs import decode_approval, parse_signing_keys
from modal_mcp.observability.redact import collect_known_secrets, redact_value
from modal_mcp.policy.approval import (
    ApprovalActor,
    ApprovalTokenLedger,
)
from modal_mcp.policy.rate_limit import TokenBucketRateLimiter, rate_limit_key
from modal_mcp.policy.rules import PolicyDecision, evaluate

MUTATING_TOOLS = frozenset(
    {
        "modal_stop_app",
        "modal_rollback_app",
        "modal_stop_container",
        "modal_terminate_sandbox",
        "modal_expert_execute",
    }
)
ActorResolver = Callable[[MiddlewareContext[Any]], ApprovalActor]


class PolicyCallArguments(BaseModel):
    """Typed policy-only fields validated before FastMCP tool validation."""

    model_config = ConfigDict(extra="allow")

    dry_run: bool = True
    approval_token: str | None = None


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Locally inferred policy metadata for a tool call."""

    tool_name: str
    toolset: str
    mutating: bool


class NullAuditSink:
    """No-op audit hook used until the structured audit sink lands."""

    def record_decision(self, *_: Any, **__: Any) -> None:
        return

    def record_error(self, *_: Any, **__: Any) -> None:
        return

    def record_result(self, *_: Any, **__: Any) -> None:
        return


class PolicyMiddleware(Middleware):
    """Apply Modal MCP policy to every FastMCP tool call."""

    def __init__(
        self,
        settings: Settings,
        *,
        approval_ledger: ApprovalTokenLedger | None = None,
        rate_limiter: TokenBucketRateLimiter | None = None,
        mutation_limiter: TokenBucketRateLimiter | None = None,
        actor_resolver: ActorResolver | None = None,
        audit_sink: Any | None = None,
        signing_keys: Sequence[tuple[str, bytes]] | None = None,
        now: Callable[[], int] | None = None,
    ) -> None:
        self.settings = settings
        self.approval_ledger = approval_ledger or ApprovalTokenLedger(
            settings.modal_mcp_approval_ledger
        )
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(
            capacity=max(1.0, settings.modal_mcp_rate_limit_rps),
            refill_rate_per_second=settings.modal_mcp_rate_limit_rps,
        )
        self.mutation_limiter = mutation_limiter or _default_mutation_limiter(settings)
        self.actor_resolver = actor_resolver or resolve_middleware_actor
        self.audit_sink = audit_sink or NullAuditSink()
        self.signing_keys = tuple(signing_keys or _signing_keys_from_settings(settings))
        self.known_secrets = collect_known_secrets(settings)
        self._now = now or (lambda: int(time.time()))
        self._argument_adapter = TypeAdapter(PolicyCallArguments)

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Run rate limit, authorization, approval, and redaction around tools."""

        params = context.message
        arguments = dict(params.arguments or {})
        policy_args = self._argument_adapter.validate_python(arguments)
        actor = self.actor_resolver(context)
        mcp_session_id = resolve_mcp_session_id(context)
        tool_policy = classify_tool(params.name)

        self._enforce_rate_limits(
            actor=actor,
            mcp_session_id=mcp_session_id,
            tool_policy=tool_policy,
        )
        decision = evaluate(
            tool_name=params.name,
            toolset=tool_policy.toolset,
            read_only=self.settings.modal_mcp_read_only,
            enabled_toolsets=self.settings.modal_mcp_enabled_toolsets,
            metadata={"mcp_session_id": mcp_session_id},
        )
        self._record("record_decision", context, decision)
        if not decision.allowed:
            raise _policy_error(decision)

        if tool_policy.mutating and not policy_args.dry_run:
            await self._consume_approval(
                token=policy_args.approval_token,
                arguments=arguments,
                actor=actor,
                mcp_session_id=mcp_session_id,
                tool_policy=tool_policy,
            )

        arguments.pop("approval_token", None)
        context = context.copy(
            message=params.model_copy(update={"arguments": arguments})
        )
        try:
            result = await call_next(context)
        except Exception as exc:
            self._record("record_error", context, params.name, exc)
            raise
        self._record("record_result", context, params.name, result)
        return redact_tool_result(result, known_secrets=self.known_secrets)

    def _enforce_rate_limits(
        self,
        *,
        actor: ApprovalActor,
        mcp_session_id: str,
        tool_policy: ToolPolicy,
    ) -> None:
        base_key = rate_limit_key(
            auth_session_id=actor.auth_session_id,
            actor_principal=actor.actor,
            mcp_session_id=mcp_session_id,
            method="tools/call",
        )
        if not self.rate_limiter.allow(f"{base_key}:tool:{tool_policy.tool_name}"):
            raise ModalAdapterError(ErrorCode.RATE_LIMITED, "tool rate limit exceeded")
        if (
            tool_policy.mutating
            and self.mutation_limiter is not None
            and not self.mutation_limiter.allow(f"mutation:{base_key}")
        ):
            raise ModalAdapterError(
                ErrorCode.RATE_LIMITED,
                "mutation rate limit exceeded",
            )

    async def _consume_approval(
        self,
        *,
        token: str | None,
        arguments: Mapping[str, Any],
        actor: ApprovalActor,
        mcp_session_id: str,
        tool_policy: ToolPolicy,
    ) -> None:
        if not token:
            raise ModalAdapterError(
                ErrorCode.POLICY_BLOCKED,
                "approval token is required for mutation execution",
            )
        try:
            payload = decode_approval(
                token,
                expected_env=self.settings.modal_environment,
                signing_keys=self.signing_keys,
                now=self._now(),
            )
        except ValueError as exc:
            raise ModalAdapterError(
                ErrorCode.POLICY_BLOCKED,
                "invalid approval token",
            ) from exc
        if payload.tool_name != tool_policy.tool_name:
            raise ModalAdapterError(
                ErrorCode.POLICY_BLOCKED,
                "approval token tool mismatch",
            )
        if (
            payload.actor != actor.actor
            or payload.auth_session_id != actor.auth_session_id
        ):
            raise ModalAdapterError(
                ErrorCode.POLICY_BLOCKED,
                "approval token actor mismatch",
            )
        if payload.mcp_session_id != mcp_session_id:
            raise ModalAdapterError(
                ErrorCode.POLICY_BLOCKED,
                "approval token MCP session mismatch",
            )
        if (
            payload.remote_mode is not None
            and payload.remote_mode != self.settings.modal_mcp_auth_mode
        ):
            raise ModalAdapterError(
                ErrorCode.POLICY_BLOCKED,
                "approval token remote mode mismatch",
            )
        target_refs = extract_target_refs(arguments)
        if target_refs and target_refs != payload.target_refs:
            raise ModalAdapterError(
                ErrorCode.POLICY_BLOCKED,
                "approval token target refs mismatch",
            )
        await self.approval_ledger.consume(token, payload, actor)

    def _record(self, method: str, *args: Any) -> None:
        hook = getattr(self.audit_sink, method, None)
        if hook is None:
            return
        hook(*args)


def classify_tool(tool_name: str) -> ToolPolicy:
    """Infer policy metadata from the server's curated tool naming scheme."""

    if tool_name in MUTATING_TOOLS:
        toolset = "expert" if tool_name.startswith("modal_expert_") else "change"
        return ToolPolicy(tool_name=tool_name, toolset=toolset, mutating=True)
    if "log" in tool_name:
        return ToolPolicy(tool_name=tool_name, toolset="logs", mutating=False)
    if "container" in tool_name:
        return ToolPolicy(tool_name=tool_name, toolset="containers", mutating=False)
    if "volume" in tool_name:
        return ToolPolicy(tool_name=tool_name, toolset="volumes", mutating=False)
    if "sandbox" in tool_name:
        return ToolPolicy(tool_name=tool_name, toolset="sandboxes", mutating=False)
    if "app" in tool_name or "deployment" in tool_name:
        return ToolPolicy(tool_name=tool_name, toolset="apps", mutating=False)
    return ToolPolicy(tool_name=tool_name, toolset="discovery", mutating=False)


def resolve_middleware_actor(context: MiddlewareContext[Any]) -> ApprovalActor:
    """Resolve actor identity from FastMCP auth, never from tool arguments."""

    access_token = get_access_token()
    if access_token is not None:
        claims = access_token.claims or {}
        actor = claims.get("sub") or access_token.client_id
        auth_session_id = claims.get("sid") or access_token.client_id
        return ApprovalActor(actor=str(actor), auth_session_id=str(auth_session_id))
    client_id = getattr(context.fastmcp_context, "client_id", None)
    if client_id:
        return ApprovalActor(actor=str(client_id), auth_session_id=str(client_id))
    raise ModalAdapterError(ErrorCode.UNAUTHORIZED, "authenticated actor is required")


def resolve_mcp_session_id(context: MiddlewareContext[Any]) -> str:
    """Resolve MCP session id from FastMCP context or the HTTP request."""

    fastmcp_context = context.fastmcp_context
    if fastmcp_context is not None:
        try:
            return str(fastmcp_context.session_id)
        except RuntimeError:
            pass
    try:
        request = get_http_request()
    except RuntimeError as exc:
        raise ModalAdapterError(
            ErrorCode.POLICY_BLOCKED,
            "MCP session id is required",
        ) from exc
    session_id = request.headers.get("mcp-session-id")
    if not session_id:
        raise ModalAdapterError(ErrorCode.POLICY_BLOCKED, "MCP session id is required")
    return session_id


def extract_target_refs(arguments: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract canonical target refs from policy-relevant arguments."""

    refs: list[str] = []
    for key, value in arguments.items():
        if key == "approval_token":
            continue
        if key == "target_refs" or key.endswith("_ref") or key.endswith("_refs"):
            _append_ref_values(refs, value)
    return tuple(sorted(refs))


def redact_tool_result(
    result: ToolResult,
    *,
    known_secrets: frozenset[str] = frozenset(),
) -> ToolResult:
    """Redact obvious secret patterns from structured tool output."""

    updates: dict[str, Any] = {}
    if result.structured_content is not None:
        updates["structured_content"] = redact_value(
            result.structured_content,
            known_secrets=known_secrets,
        )
    if result.meta is not None:
        updates["meta"] = redact_value(result.meta, known_secrets=known_secrets)
    if not updates:
        return result
    return result.model_copy(update=updates)


def _append_ref_values(refs: list[str], value: Any) -> None:
    if isinstance(value, str):
        refs.append(value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        refs.extend(str(item) for item in value)


def _default_mutation_limiter(
    settings: Settings,
) -> TokenBucketRateLimiter | None:
    seconds = settings.modal_mcp_mutation_rate_limit_seconds
    if seconds <= 0:
        return None
    return TokenBucketRateLimiter(
        capacity=1.0,
        refill_rate_per_second=1.0 / seconds,
    )


def _signing_keys_from_settings(settings: Settings) -> tuple[tuple[str, bytes], ...]:
    raw_keys = settings.modal_mcp_signing_keys
    if raw_keys is None:
        return ()
    return parse_signing_keys(raw_keys.get_secret_value())


def _policy_error(decision: PolicyDecision) -> ModalAdapterError:
    code = (
        ErrorCode.POLICY_BLOCKED
        if decision.code.value != ErrorCode.RATE_LIMITED.value
        else ErrorCode.RATE_LIMITED
    )
    return ModalAdapterError(code, decision.reason, details={"policy": decision.code})


__all__ = [
    "MUTATING_TOOLS",
    "NullAuditSink",
    "PolicyCallArguments",
    "PolicyMiddleware",
    "ToolPolicy",
    "classify_tool",
    "extract_target_refs",
    "redact_tool_result",
    "redact_value",
    "resolve_mcp_session_id",
    "resolve_middleware_actor",
]
