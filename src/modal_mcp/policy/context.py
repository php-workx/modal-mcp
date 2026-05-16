"""Pre-bound policy dependencies built once at server startup."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Self

from fastmcp.server.middleware import MiddlewareContext

from modal_mcp.config import ConfigError, Settings
from modal_mcp.domain.refs import parse_signing_keys
from modal_mcp.observability.audit import audit_sink_from_settings
from modal_mcp.policy.approval import ApprovalActor, ApprovalTokenLedger
from modal_mcp.policy.audit import AuditSink, NullAuditSink
from modal_mcp.policy.rate_limit import TokenBucketRateLimiter

# Forward type alias matching engine.ActorResolver to avoid a circular import.
# engine.py imports PolicyContext (this module), not the other way around.
ActorResolver = Callable[[MiddlewareContext[Any]], ApprovalActor]


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Pre-bound policy dependencies built once at server startup.

    Holding every dependency as a value (never a factory) makes
    ``PolicyMiddleware.__init__`` a three-argument assignment with zero
    branching.  Misconfiguration surfaces here, at startup, with a clean
    traceback to :meth:`from_settings` — not on the first tool call.
    """

    approval_ledger: ApprovalTokenLedger
    rate_limiter: TokenBucketRateLimiter
    mutation_limiter: TokenBucketRateLimiter | None
    actor_resolver: ActorResolver
    audit_sink: AuditSink
    signing_keys: tuple[tuple[str, bytes], ...]
    now: Callable[[], int]

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Build a fully-bound PolicyContext from validated Settings.

        Raises:
            ConfigError: when an invariant fails at startup
                (e.g. mutating mode without signing keys).
        """

        from modal_mcp.policy.engine import resolve_middleware_actor

        approval_ledger = ApprovalTokenLedger(settings.modal_mcp_approval_ledger)
        rate_limiter = TokenBucketRateLimiter(
            capacity=max(1.0, settings.modal_mcp_rate_limit_rps),
            refill_rate_per_second=settings.modal_mcp_rate_limit_rps,
        )
        mutation_limiter = _build_mutation_limiter(settings)
        signing_keys = _build_signing_keys(settings)
        audit_sink = audit_sink_from_settings(settings)
        return cls(
            approval_ledger=approval_ledger,
            rate_limiter=rate_limiter,
            mutation_limiter=mutation_limiter,
            actor_resolver=resolve_middleware_actor,
            audit_sink=audit_sink,
            signing_keys=signing_keys,
            now=lambda: int(time.time()),
        )


def _build_mutation_limiter(settings: Settings) -> TokenBucketRateLimiter | None:
    seconds = settings.modal_mcp_mutation_rate_limit_seconds
    if seconds <= 0:
        return None
    return TokenBucketRateLimiter(
        capacity=1.0,
        refill_rate_per_second=1.0 / seconds,
    )


def _build_signing_keys(settings: Settings) -> tuple[tuple[str, bytes], ...]:
    raw_keys = settings.modal_mcp_signing_keys
    if raw_keys is None:
        if not settings.modal_mcp_read_only:
            msg = (
                "policy context: signing keys are required when "
                "MODAL_MCP_READ_ONLY=false (mutating tools must be able to "
                "verify approval tokens at call time)"
            )
            raise ConfigError(msg)
        return ()
    try:
        parsed = parse_signing_keys(raw_keys.get_secret_value())
    except ValueError as exc:
        msg = "policy context: MODAL_MCP_SIGNING_KEYS is malformed"
        raise ConfigError(msg) from exc
    return tuple(parsed)


__all__ = [
    "ActorResolver",
    "NullAuditSink",  # re-exported so callers don't have to import audit.py
    "PolicyContext",
]
