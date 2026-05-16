"""FastMCP server and ASGI composition for Modal MCP."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from modal_mcp.adapters.modal_adapter import ModalSdkAdapter
from modal_mcp.adapters.registry import bind_modal_adapter
from modal_mcp.asgi import OriginGuard
from modal_mcp.auth import STATIC_BEARER_SCOPE, StaticTokenVerifier, build_auth
from modal_mcp.config import (
    Settings,
    assert_runtime_security,
    load_secret_file,
    scrub_secret_env,
)
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.domain.refs import parse_signing_keys
from modal_mcp.observability.audit import audit_sink_from_settings
from modal_mcp.observability.logger import configure_logging
from modal_mcp.observability.tracing import OtelMiddleware
from modal_mcp.policy.approval import (
    RECORD_APPROVED,
    ApprovalActor,
    ApprovalRecord,
    ApprovalTokenLedger,
    resolve_http_actor,
    validate_approval_http_request,
)
from modal_mcp.policy.context import PolicyContext
from modal_mcp.policy.engine import PolicyMiddleware
from modal_mcp.policy.rate_limit import TokenBucketRateLimiter, rate_limit_key
from modal_mcp.toolsets import register_toolsets

logger = logging.getLogger(__name__)

ALL_TOOLSETS = frozenset(
    {
        "discovery",
        "apps",
        "containers",
        "logs",
        "volumes",
        "sandboxes",
        "expert",
    }
)

AdapterFactory = Callable[[Settings], Awaitable[Any]]
SettingsFactory = Callable[[], Settings]


async def _default_adapter_factory(settings: Settings) -> ModalSdkAdapter:
    """Create the production Modal SDK adapter."""

    return await ModalSdkAdapter.create(settings)


def _approval_ledger_from_settings(settings: Settings) -> ApprovalTokenLedger:
    """Create the approval ledger backing the HTTP approval route."""

    return ApprovalTokenLedger(settings.modal_mcp_approval_ledger)


def _approval_signing_keys_from_settings(
    settings: Settings,
) -> tuple[tuple[str, bytes], ...]:
    """Parse configured approval signing keys from settings."""

    raw_keys = settings.modal_mcp_signing_keys
    if raw_keys is None:
        raise ModalAdapterError(
            ErrorCode.UNAUTHORIZED,
            "configured signing keys are required for approval",
        )

    try:
        parsed = parse_signing_keys(raw_keys.get_secret_value())
    except ValueError as exc:
        raise ModalAdapterError(
            ErrorCode.INTERNAL_DRIFT,
            "configured signing keys are malformed",
        ) from exc
    if not parsed:
        raise ModalAdapterError(
            ErrorCode.UNAUTHORIZED,
            "configured signing keys are required for approval",
        )
    return tuple(parsed)


def _approval_response(record: ApprovalRecord) -> dict[str, Any]:
    """Return a sanitized approval response body."""

    return {
        "ok": True,
        "approval": {
            "status": record.status,
            "token_digest": record.token_digest,
            "actor": record.actor,
            "auth_session_id": record.auth_session_id,
            "mcp_session_id": record.mcp_session_id,
            "tool_name": record.tool_name,
            "workspace": record.workspace,
            "expires_at": record.expires_at,
        },
    }


def _approval_error_response(error: ModalAdapterError) -> JSONResponse:
    """Return a sanitized HTTP error response for approval failures."""

    if error.code == ErrorCode.UNAUTHORIZED:
        status_code = 401
    elif error.code == ErrorCode.RATE_LIMITED:
        status_code = 429
    elif error.code == ErrorCode.INTERNAL_DRIFT:
        status_code = 500
    else:
        status_code = 403
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": error.to_payload().model_dump()},
    )


def _mark_approval_audit_failure(
    ledger: ApprovalTokenLedger,
    record: ApprovalRecord,
) -> Awaitable[ApprovalRecord]:
    """Move a staged approval into a terminal non-usable state."""

    return ledger.mark_approval_unusable(record)


def _approval_rate_limiter_from_settings(
    settings: Settings,
) -> TokenBucketRateLimiter | None:
    seconds = settings.modal_mcp_mutation_rate_limit_seconds
    if seconds <= 0:
        return None
    return TokenBucketRateLimiter(
        capacity=1.0,
        refill_rate_per_second=1.0 / seconds,
    )


def _enforce_approval_rate_limit(
    rate_limiter: TokenBucketRateLimiter | None,
    actor: ApprovalActor,
) -> None:
    if rate_limiter is None:
        return
    key = rate_limit_key(
        auth_session_id=actor.auth_session_id,
        actor_principal=actor.actor,
        method="approval",
    )
    if not rate_limiter.allow(f"mutation:{key}:approval"):
        raise ModalAdapterError(
            ErrorCode.RATE_LIMITED,
            "approval rate limit exceeded",
        )


def _record_approval_denial(
    audit_sink: Any,
    error: ModalAdapterError,
    *,
    actor: ApprovalActor | None,
    request: Request,
) -> None:
    hook = getattr(audit_sink, "record_approval_denial", None)
    if hook is None:
        return
    with suppress(Exception):
        hook(
            error,
            actor=actor.actor if actor is not None else None,
            auth_session_id=actor.auth_session_id if actor is not None else None,
            mcp_session_id=request.headers.get("mcp-session-id"),
            path=request.url.path,
        )


async def _resolve_route_actor(request: Request, settings: Settings) -> ApprovalActor:
    """Resolve the approval actor from trusted auth context or bearer auth."""

    try:
        return resolve_http_actor(request)
    except ModalAdapterError as exc:
        if exc.code != ErrorCode.UNAUTHORIZED:
            raise

    token_file = settings.modal_mcp_self_hosted_bearer_token_file
    if token_file is None:
        raise ModalAdapterError(
            ErrorCode.UNAUTHORIZED,
            "authenticated actor is required for approval",
        )

    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise ModalAdapterError(
            ErrorCode.UNAUTHORIZED,
            "authenticated actor is required for approval",
        )
    bearer_token = authorization.partition(" ")[2].strip()
    if not bearer_token:
        raise ModalAdapterError(
            ErrorCode.UNAUTHORIZED,
            "authenticated actor is required for approval",
        )

    verifier = StaticTokenVerifier(
        load_secret_file(token_file),
        required_scopes=[STATIC_BEARER_SCOPE],
    )
    access_token = await verifier.verify_token(bearer_token)
    if access_token is None:
        raise ModalAdapterError(
            ErrorCode.UNAUTHORIZED,
            "authenticated actor is required for approval",
        )

    claims = access_token.claims or {}
    actor = claims.get("sub") or access_token.client_id
    auth_session_id = claims.get("sid") or access_token.client_id
    if not actor or not auth_session_id:
        raise ModalAdapterError(
            ErrorCode.UNAUTHORIZED,
            "authenticated actor is required for approval",
        )

    request.scope["user"] = SimpleNamespace(access_token=access_token)
    return ApprovalActor(actor=str(actor), auth_session_id=str(auth_session_id))


def _approval_route(
    settings: Settings,
    ledger: ApprovalTokenLedger,
    audit_sink: Any,
    rate_limiter: TokenBucketRateLimiter | None,
) -> Route:
    """Build the standalone approval endpoint route."""

    async def endpoint(request: Request) -> JSONResponse:
        actor: ApprovalActor | None = None
        try:
            actor = await _resolve_route_actor(request, settings)
            request.scope["modal_mcp.actor_context"] = actor
            _enforce_approval_rate_limit(rate_limiter, actor)
            candidate = await validate_approval_http_request(
                request,
                settings=settings,
                signing_keys=_approval_signing_keys_from_settings(settings),
            )
            pending = await ledger.begin_approval(
                candidate.token,
                candidate.payload,
                candidate.actor,
            )
        except ModalAdapterError as exc:
            _record_approval_denial(
                audit_sink,
                exc,
                actor=actor,
                request=request,
            )
            return _approval_error_response(exc)
        except Exception as exc:  # pragma: no cover - defensive response wrapper
            return _approval_error_response(
                ModalAdapterError(
                    ErrorCode.INTERNAL_DRIFT,
                    "approval endpoint failed",
                    debug={"exception": type(exc).__name__},
                )
            )

        try:
            audit_sink.record_approval(
                "approved",
                replace(pending, status=RECORD_APPROVED),
            )
        except Exception as exc:  # pragma: no cover - defensive response wrapper
            try:
                await _mark_approval_audit_failure(ledger, pending)
            except Exception:
                logger.exception("failed to mark approval unusable after audit failure")
            return _approval_error_response(
                ModalAdapterError(
                    ErrorCode.INTERNAL_DRIFT,
                    "approval endpoint failed",
                    debug={"exception": type(exc).__name__},
                )
            )

        try:
            record = await ledger.commit_approval(pending)
        except Exception as exc:  # pragma: no cover - defensive response wrapper
            return _approval_error_response(
                ModalAdapterError(
                    ErrorCode.INTERNAL_DRIFT,
                    "approval endpoint failed",
                    debug={"exception": type(exc).__name__},
                )
            )

        return JSONResponse(_approval_response(record))

    return Route("/mcp/approvals/{token}", endpoint=endpoint, methods=["POST"])


def _split_bind(bind: str) -> tuple[str, int]:
    host, separator, port_text = bind.rpartition(":")
    if not separator or not host or not port_text:
        msg = "MODAL_MCP_HTTP_BIND must be formatted as host:port"
        raise ValueError(msg)
    return host, int(port_text)


def _settings_from_env() -> Settings:
    settings_factory = cast(SettingsFactory, Settings)
    return settings_factory()


@asynccontextmanager
async def fastmcp_lifespan(
    server: FastMCP[Any],
    *,
    settings: Settings,
    adapter_factory: AdapterFactory = _default_adapter_factory,
) -> AsyncIterator[None]:
    """Bind the process-wide Modal adapter for the FastMCP lifespan."""

    del server
    adapter = await adapter_factory(settings)
    bind_modal_adapter(adapter)
    try:
        yield
    finally:
        bind_modal_adapter(None)
        close = getattr(adapter, "aclose", None)
        if close is not None:
            result = close()
            if isinstance(result, Awaitable):
                await result


def create_mcp(
    settings: Settings | None = None,
    *,
    adapter_factory: AdapterFactory = _default_adapter_factory,
    approval_ledger: ApprovalTokenLedger | None = None,
    audit_sink: Any | None = None,
    _skip_security_check: bool = False,
) -> FastMCP[Any]:
    """Create the FastMCP server with auth, lifespan, and toolset gating."""

    resolved_settings = settings or _settings_from_env()
    if not _skip_security_check:
        assert_runtime_security(resolved_settings)
    configure_logging(resolved_settings)
    resolved_audit_sink = audit_sink or audit_sink_from_settings(resolved_settings)

    @asynccontextmanager
    async def lifespan(server: FastMCP[Any]) -> AsyncIterator[None]:
        async with fastmcp_lifespan(
            server,
            settings=resolved_settings,
            adapter_factory=adapter_factory,
        ):
            yield

    mcp: FastMCP[Any] = FastMCP(
        name="modal-mcp",
        version="0.1.0",
        lifespan=lifespan,
        auth=build_auth(resolved_settings),
    )
    mcp.add_middleware(OtelMiddleware(resolved_settings))

    policy_context = PolicyContext.from_settings(resolved_settings)
    if approval_ledger is not None:
        policy_context = replace(policy_context, approval_ledger=approval_ledger)
    if resolved_audit_sink is not None:
        policy_context = replace(policy_context, audit_sink=resolved_audit_sink)

    mcp.add_middleware(PolicyMiddleware(mcp, policy_context, resolved_settings))
    register_toolsets(mcp, resolved_settings)

    disabled_toolsets = ALL_TOOLSETS - set(resolved_settings.modal_mcp_enabled_toolsets)
    if disabled_toolsets:
        mcp.disable(tags=set(disabled_toolsets))
    if resolved_settings.modal_mcp_read_only:
        mcp.disable(tags={"expert"})
    return mcp


def create_asgi_app(
    settings: Settings | None = None,
    *,
    adapter_factory: AdapterFactory = _default_adapter_factory,
) -> Starlette:
    """Create the externally mounted Starlette ASGI application."""

    resolved_settings = settings or _settings_from_env()
    assert_runtime_security(resolved_settings)
    scrub_secret_env()
    approval_ledger = _approval_ledger_from_settings(resolved_settings)
    audit_sink = audit_sink_from_settings(resolved_settings)
    approval_rate_limiter = _approval_rate_limiter_from_settings(resolved_settings)
    mcp = create_mcp(
        resolved_settings,
        adapter_factory=adapter_factory,
        approval_ledger=approval_ledger,
        audit_sink=audit_sink,
        _skip_security_check=True,
    )
    # Validate origin/host allowlists eagerly so configuration mistakes raise
    # ConfigError at startup instead of surfacing as silent per-request 403s.
    # Starlette instantiates Middleware lazily on first request, so we must
    # exercise OriginGuard's construction-time validation here ourselves.
    OriginGuard._build_allowed_set(
        resolved_settings.modal_mcp_allowed_origins, kind="origin"
    )
    OriginGuard._build_allowed_set(
        resolved_settings.modal_mcp_allowed_hosts, kind="host"
    )
    middleware = [
        Middleware(
            OriginGuard,
            allowed_origins=resolved_settings.modal_mcp_allowed_origins,
            allowed_hosts=resolved_settings.modal_mcp_allowed_hosts,
        )
    ]
    mcp_app = mcp.http_app(path="/mcp", middleware=middleware)
    app = Starlette(
        routes=[
            _approval_route(
                resolved_settings,
                approval_ledger,
                audit_sink,
                approval_rate_limiter,
            ),
            Mount("/", app=mcp_app),
        ],
        lifespan=mcp_app.lifespan,
    )
    app.state.approval_ledger = approval_ledger
    app.state.approval_audit_sink = audit_sink
    app.state.approval_rate_limiter = approval_rate_limiter
    app.state.policy_approval_ledger = approval_ledger
    app.state.policy_audit_sink = audit_sink
    app.state.mcp = mcp
    return app


def run(settings: Settings | None = None) -> None:
    """Run the Modal MCP server with uvicorn."""

    resolved_settings = settings or _settings_from_env()
    host, port = _split_bind(resolved_settings.modal_mcp_http_bind)
    uvicorn.run(create_asgi_app(resolved_settings), host=host, port=port)


def run_stdio(settings: Settings | None = None) -> None:
    """Run the Modal MCP server over stdin/stdout (stdio transport).

    Used by CLI clients such as Codex that spawn the server as a subprocess
    and communicate via the MCP stdio transport rather than HTTP.  Auth and
    the HTTP approval route are not applicable here; everything else that
    ``create_mcp`` composes (``assert_runtime_security``, ``PolicyMiddleware``,
    ``OtelMiddleware``, redaction, rate limiting, mutation gating, the
    audit sink, the adapter lifespan, tool filtering, and the read-only
    posture) MUST stay wired or stdio launches will ship a silent security
    regression versus the HTTP transport.

    Implementation note: this reuses ``create_mcp`` directly rather than
    rebuilding a fresh ``FastMCP`` so the two transports cannot drift on
    middleware, toolset gating, or runtime-security checks.  ``scrub_secret_env``
    is invoked here for parity with ``create_asgi_app`` — both transports
    should clear sensitive env vars from the process before the server begins
    accepting requests.
    """

    resolved_settings = settings or _settings_from_env()
    scrub_secret_env()
    mcp = create_mcp(resolved_settings)
    mcp.run(transport="stdio")


__all__ = [
    "ALL_TOOLSETS",
    "create_asgi_app",
    "create_mcp",
    "fastmcp_lifespan",
    "run",
    "run_stdio",
]
