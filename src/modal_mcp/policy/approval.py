"""Approval-token ledger and out-of-band approval verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlette.requests import Request

from modal_mcp.asgi import OriginValidationError, validate_origin
from modal_mcp.config import Settings
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.domain.refs import ApprovalPayload, decode_approval

APPROVAL_CONFIRMATION_HEADER = "x-modal-mcp-confirm-approval"
APPROVAL_CONFIRMATION_VALUE = "approve"
SAFE_FETCH_SITES = frozenset({"same-origin", "same-site"})
RECORD_APPROVED = "approved"
RECORD_CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class ApprovalActor:
    """Authenticated actor bound to an approval request."""

    actor: str
    auth_session_id: str


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """Persisted approval-token state."""

    token_digest: str
    status: str
    actor: str
    auth_session_id: str
    mcp_session_id: str
    tool_name: str
    workspace: str
    expires_at: int


class ApprovalTokenLedger:
    """Single-use approval-token ledger with optional fsync-backed persistence."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        now: Callable[[], int] | None = None,
    ) -> None:
        self.path = Path(path).expanduser() if path is not None else None
        self._now = now or (lambda: int(time.time()))
        self._lock = asyncio.Lock()
        self._approved: dict[str, ApprovalRecord] = {}
        self._consumed: dict[str, ApprovalRecord] = {}
        if self.path is not None:
            self._load()

    async def approve(
        self,
        token: str,
        payload: ApprovalPayload,
        actor: ApprovalActor,
    ) -> ApprovalRecord:
        """Record a human approval exactly once for a token."""

        token_digest = token_sha256(token)
        async with self._lock:
            self._reject_expired(payload)
            if token_digest in self._consumed:
                raise _policy_blocked("approval token has already been consumed")
            if token_digest in self._approved:
                raise _policy_blocked("approval token has already been approved")
            record = _record_from_payload(
                token_digest=token_digest,
                status=RECORD_APPROVED,
                payload=payload,
                actor=actor,
            )
            self._append(record)
            self._approved[token_digest] = record
            return record

    async def consume(
        self,
        token: str,
        payload: ApprovalPayload,
        actor: ApprovalActor,
    ) -> ApprovalRecord:
        """Atomically consume a previously approved token."""

        token_digest = token_sha256(token)
        async with self._lock:
            self._reject_expired(payload)
            if token_digest in self._consumed:
                raise _policy_blocked("approval token has already been consumed")
            approved = self._approved.get(token_digest)
            if approved is None:
                raise _policy_blocked("approval token has not been approved")
            _assert_actor_matches(payload, actor)
            record = _record_from_payload(
                token_digest=token_digest,
                status=RECORD_CONSUMED,
                payload=payload,
                actor=actor,
            )
            self._append(record)
            self._consumed[token_digest] = record
            return approved

    def is_approved(self, token: str) -> bool:
        """Return whether a token has an approval record."""

        return token_sha256(token) in self._approved

    def is_consumed(self, token: str) -> bool:
        """Return whether a token has been consumed."""

        return token_sha256(token) in self._consumed

    def _reject_expired(self, payload: ApprovalPayload) -> None:
        if payload.exp <= self._now():
            raise _policy_blocked("approval token has expired")

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            record = ApprovalRecord(
                token_digest=str(raw["token_digest"]),
                status=str(raw["status"]),
                actor=str(raw["actor"]),
                auth_session_id=str(raw["auth_session_id"]),
                mcp_session_id=str(raw["mcp_session_id"]),
                tool_name=str(raw["tool_name"]),
                workspace=str(raw["workspace"]),
                expires_at=int(raw["expires_at"]),
            )
            if record.status == RECORD_APPROVED:
                self._approved[record.token_digest] = record
            elif record.status == RECORD_CONSUMED:
                self._consumed[record.token_digest] = record

    def _append(self, record: ApprovalRecord) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "token_digest": record.token_digest,
            "status": record.status,
            "actor": record.actor,
            "auth_session_id": record.auth_session_id,
            "mcp_session_id": record.mcp_session_id,
            "tool_name": record.tool_name,
            "workspace": record.workspace,
            "expires_at": record.expires_at,
            "recorded_at": self._now(),
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())


class RedisApprovalTokenLedger:
    """Placeholder for hosted/multi-worker Redis SET NX PX approval state."""

    def __init__(self, *_: Any, **__: Any) -> None:
        msg = "Redis approval ledger is not implemented in v1"
        raise NotImplementedError(msg)


async def approve_http_request(
    request: Request,
    *,
    ledger: ApprovalTokenLedger,
    settings: Settings,
    approval_token: str | None = None,
    expected_env: str | None = None,
    signing_keys: Sequence[tuple[str, bytes]] | None = None,
    now: int | None = None,
) -> ApprovalRecord:
    """Validate and record an out-of-band approval HTTP request."""

    _validate_transport_controls(request, settings)
    actor = resolve_http_actor(request)
    mcp_session_id = request.headers.get("mcp-session-id")
    if not mcp_session_id:
        raise _policy_blocked("missing Mcp-Session-Id header")

    body = await _json_body(request)
    token = approval_token or request.path_params.get("token") or body.get("token")
    if not isinstance(token, str) or not token:
        raise _policy_blocked("missing approval token")

    if not _has_confirmation_marker(request, body):
        raise _policy_blocked("missing approval confirmation marker")

    try:
        payload = decode_approval(
            token,
            expected_env=expected_env or settings.modal_environment,
            signing_keys=signing_keys,
            now=now,
        )
    except ValueError as exc:
        raise _policy_blocked("invalid approval token") from exc

    _assert_actor_matches(payload, actor)
    if (
        payload.remote_mode is not None
        and payload.remote_mode != settings.modal_mcp_auth_mode
    ):
        raise _policy_blocked("approval token remote mode mismatch")
    if payload.mcp_session_id != mcp_session_id:
        raise _policy_blocked("approval token MCP session mismatch")
    _assert_optional_scope("tool_name", body, payload.tool_name)
    _assert_optional_scope("workspace", body, payload.ws)
    _assert_optional_target_refs(body, payload)
    return await ledger.approve(token, payload, actor)


def resolve_http_actor(request: Request) -> ApprovalActor:
    """Resolve actor identity from trusted request auth context."""

    scoped = request.scope.get("modal_mcp.actor_context")
    if isinstance(scoped, ApprovalActor):
        return scoped
    if isinstance(scoped, Mapping):
        actor = scoped.get("actor")
        auth_session_id = scoped.get("auth_session_id")
        if actor and auth_session_id:
            return ApprovalActor(actor=str(actor), auth_session_id=str(auth_session_id))

    user = request.scope.get("user")
    access_token = getattr(user, "access_token", None)
    if access_token is not None:
        claims = getattr(access_token, "claims", {}) or {}
        actor = claims.get("sub") or getattr(access_token, "client_id", None)
        auth_session_id = claims.get("sid") or getattr(access_token, "client_id", None)
        if actor and auth_session_id:
            return ApprovalActor(actor=str(actor), auth_session_id=str(auth_session_id))

    raise ModalAdapterError(
        ErrorCode.UNAUTHORIZED,
        "authenticated actor is required for approval",
    )


def token_sha256(token: str) -> str:
    """Digest a token for ledger storage without persisting bearer material."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_transport_controls(request: Request, settings: Settings) -> None:
    try:
        validate_origin(
            request.headers.get("origin"),
            request.headers.get("host"),
            settings,
        )
    except OriginValidationError as exc:
        raise _policy_blocked(str(exc)) from exc

    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site.lower() not in SAFE_FETCH_SITES:
        raise _policy_blocked("cross-site approval requests are rejected")


async def _json_body(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        return {}
    raw = await request.json()
    return raw if isinstance(raw, dict) else {}


def _has_confirmation_marker(request: Request, body: Mapping[str, Any]) -> bool:
    header = request.headers.get(APPROVAL_CONFIRMATION_HEADER)
    if header and header.lower() == APPROVAL_CONFIRMATION_VALUE:
        return True
    marker = body.get("confirmation") or body.get("confirm")
    return isinstance(marker, str) and marker.lower() == APPROVAL_CONFIRMATION_VALUE


def _assert_actor_matches(payload: ApprovalPayload, actor: ApprovalActor) -> None:
    if payload.actor != actor.actor:
        raise _policy_blocked("approval token actor mismatch")
    if payload.auth_session_id != actor.auth_session_id:
        raise _policy_blocked("approval token auth session mismatch")


def _assert_optional_scope(name: str, body: Mapping[str, Any], expected: str) -> None:
    value = body.get(name)
    if value is not None and str(value) != expected:
        raise _policy_blocked(f"approval token {name} mismatch")


def _assert_optional_target_refs(
    body: Mapping[str, Any],
    payload: ApprovalPayload,
) -> None:
    raw_refs = body.get("target_refs")
    if raw_refs is None:
        return
    refs: tuple[str, ...]
    if isinstance(raw_refs, str):
        refs = (raw_refs,)
    elif isinstance(raw_refs, Sequence):
        refs = tuple(str(item) for item in raw_refs)
    else:
        raise _policy_blocked("approval target_refs must be a list")
    if tuple(sorted(refs)) != payload.target_refs:
        raise _policy_blocked("approval token target refs mismatch")


def _record_from_payload(
    *,
    token_digest: str,
    status: str,
    payload: ApprovalPayload,
    actor: ApprovalActor,
) -> ApprovalRecord:
    return ApprovalRecord(
        token_digest=token_digest,
        status=status,
        actor=actor.actor,
        auth_session_id=actor.auth_session_id,
        mcp_session_id=payload.mcp_session_id,
        tool_name=payload.tool_name,
        workspace=payload.ws,
        expires_at=payload.exp,
    )


def _policy_blocked(message: str) -> ModalAdapterError:
    return ModalAdapterError(ErrorCode.POLICY_BLOCKED, message)


__all__ = [
    "APPROVAL_CONFIRMATION_HEADER",
    "APPROVAL_CONFIRMATION_VALUE",
    "ApprovalActor",
    "ApprovalRecord",
    "ApprovalTokenLedger",
    "RedisApprovalTokenLedger",
    "approve_http_request",
    "resolve_http_actor",
    "token_sha256",
]
