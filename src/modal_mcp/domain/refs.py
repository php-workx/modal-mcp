"""Signed ref, cursor, and approval token codecs.

The token wire format is:

```
<prefix>.<canonical-cbor-payload>.<hmac>
```

The payload is canonical CBOR and the HMAC input is:

```
HMAC-SHA256(
    K,
    "modal-mcp/v1" || 0x00 || type_tag || 0x00 || keyid || 0x00 || payload
)
```

where the signing keys come from ``MODAL_MCP_SIGNING_KEYS``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import cbor2
from pydantic import BaseModel, ConfigDict, field_validator

MAC_CONTEXT = b"modal-mcp/v1"
TOKEN_VERSION = 1
REF_PREFIX = "mref1"
CURSOR_PREFIX = "mc1"
APPROVAL_PREFIX = "mappr1"


@dataclass(frozen=True, slots=True)
class _SigningKey:
    """Parsed HMAC key material."""

    kid: str
    key: bytes


class _TokenPayload(BaseModel):
    """Shared fields for all signed token payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["ref", "cursor", "approval"]
    env: str
    ws: str
    v: int = TOKEN_VERSION
    exp: int


class RefPayload(_TokenPayload):
    """Payload carried by a signed ref token."""

    kind: Literal["ref"] = "ref"
    id: str


class CursorPayload(_TokenPayload):
    """Payload carried by a signed pagination cursor."""

    kind: Literal["cursor"] = "cursor"
    cursor: str


class ApprovalPayload(_TokenPayload):
    """Payload carried by a signed approval token."""

    kind: Literal["approval"] = "approval"
    tool_name: str
    target_refs: tuple[str, ...]
    actor: str
    mcp_session_id: str
    auth_session_id: str
    nonce: str

    @field_validator("target_refs", mode="before")
    @classmethod
    def _sort_target_refs(cls, value: Any) -> tuple[str, ...]:
        """Normalize approval targets into deterministic order."""

        if isinstance(value, str):
            value = (value,)
        if isinstance(value, Sequence):
            return tuple(sorted(str(item) for item in value))
        msg = "target_refs must be a sequence of strings"
        raise TypeError(msg)


def canonical_cbor_deterministic(payload: Mapping[str, Any] | BaseModel) -> bytes:
    """Encode a payload using deterministic canonical CBOR."""

    if isinstance(payload, BaseModel):
        payload_data = payload.model_dump(mode="python")
    else:
        payload_data = dict(payload)
    return cbor2.dumps(payload_data, canonical=True)


def _encode_base64url(value: bytes) -> str:
    """Encode bytes using unpadded URL-safe base64."""

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    """Decode unpadded URL-safe base64 bytes."""

    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _load_signing_keys(raw: str | None = None) -> tuple[_SigningKey, ...]:
    """Parse signing keys from the configured comma-separated secret."""

    if raw is None:
        raw = os.environ.get("MODAL_MCP_SIGNING_KEYS")
    if raw is None or not raw.strip():
        msg = "MODAL_MCP_SIGNING_KEYS is required"
        raise ValueError(msg)

    keys: list[_SigningKey] = []
    for item in raw.split(","):
        entry = item.strip()
        if not entry:
            continue
        if ":" not in entry:
            msg = f"invalid signing key entry: {entry!r}"
            raise ValueError(msg)
        kid, hex_key = entry.split(":", 1)
        kid = kid.strip()
        hex_key = hex_key.strip()
        if not kid or not hex_key:
            msg = f"invalid signing key entry: {entry!r}"
            raise ValueError(msg)
        try:
            key_bytes = bytes.fromhex(hex_key)
        except ValueError as exc:
            msg = f"invalid hex signing key for kid {kid!r}"
            raise ValueError(msg) from exc
        keys.append(_SigningKey(kid=kid, key=key_bytes))

    if not keys:
        msg = "MODAL_MCP_SIGNING_KEYS did not contain any keys"
        raise ValueError(msg)
    return tuple(keys)


def _coerce_signing_keys(
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[_SigningKey] | None,
) -> tuple[_SigningKey, ...]:
    """Normalize explicit signing keys or load them from the environment."""

    if signing_keys is None:
        return _load_signing_keys()

    keys: list[_SigningKey] = []
    for item in signing_keys:
        if isinstance(item, _SigningKey):
            keys.append(item)
            continue
        kid, key = item
        keys.append(_SigningKey(kid=str(kid), key=bytes(key)))

    if not keys:
        msg = "at least one signing key is required"
        raise ValueError(msg)
    return tuple(keys)


def _mac(type_tag: str, signing_key: _SigningKey, payload: bytes) -> bytes:
    """Compute the HMAC for a token payload."""

    mac_input = (
        MAC_CONTEXT
        + b"\x00"
        + type_tag.encode("ascii")
        + b"\x00"
        + signing_key.kid.encode("ascii")
        + b"\x00"
        + payload
    )
    return hmac.new(signing_key.key, mac_input, hashlib.sha256).digest()


def _encode_token(
    payload: _TokenPayload,
    *,
    prefix: str,
    type_tag: str,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[_SigningKey] | None = None,
) -> str:
    """Encode and sign a payload into its compact token form."""

    keys = _coerce_signing_keys(signing_keys)
    payload_bytes = canonical_cbor_deterministic(payload)
    signature = _mac(type_tag, keys[0], payload_bytes)
    return f"{prefix}.{_encode_base64url(payload_bytes)}.{_encode_base64url(signature)}"


def _decode_token[T: _TokenPayload](
    token: str,
    *,
    prefix: str,
    type_tag: str,
    payload_type: type[T],
    expected_env: str | None = None,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[_SigningKey] | None = None,
    now: int | None = None,
) -> T:
    """Validate a token, its signature, and its canonical CBOR payload."""

    parts = token.split(".")
    if len(parts) != 3 or parts[0] != prefix:
        msg = f"invalid token prefix: {token!r}"
        raise ValueError(msg)

    payload_bytes = _decode_base64url(parts[1])
    signature = _decode_base64url(parts[2])

    try:
        raw_payload = cbor2.loads(payload_bytes)
    except Exception as exc:  # pragma: no cover - defensive CBOR failure path
        msg = "invalid CBOR payload"
        raise ValueError(msg) from exc

    payload = payload_type.model_validate(raw_payload)
    if canonical_cbor_deterministic(payload) != payload_bytes:
        msg = "non-canonical token payload"
        raise ValueError(msg)

    if payload.kind != type_tag:
        msg = f"unexpected token kind: {payload.kind!r}"
        raise ValueError(msg)

    if payload.v != TOKEN_VERSION:
        msg = f"unsupported token version: {payload.v}"
        raise ValueError(msg)

    if expected_env is not None and payload.env != expected_env:
        msg = f"token env mismatch: expected {expected_env!r}, got {payload.env!r}"
        raise ValueError(msg)

    current_time = int(now if now is not None else time.time())
    if payload.exp <= current_time:
        msg = "token has expired"
        raise ValueError(msg)

    keys = _coerce_signing_keys(signing_keys)
    for signing_key in keys:
        expected_signature = _mac(type_tag, signing_key, payload_bytes)
        if hmac.compare_digest(expected_signature, signature):
            return payload

    msg = "invalid token signature"
    raise ValueError(msg)


def encode_ref(
    payload: RefPayload,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[_SigningKey] | None = None,
) -> str:
    """Encode a signed ref token."""

    return _encode_token(
        payload,
        prefix=REF_PREFIX,
        type_tag="ref",
        signing_keys=signing_keys,
    )


def decode_ref(
    token: str,
    *,
    expected_env: str | None = None,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[_SigningKey] | None = None,
    now: int | None = None,
) -> RefPayload:
    """Decode and validate a signed ref token."""

    return _decode_token(
        token,
        prefix=REF_PREFIX,
        type_tag="ref",
        payload_type=RefPayload,
        expected_env=expected_env,
        signing_keys=signing_keys,
        now=now,
    )


def encode_cursor(
    payload: CursorPayload,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[_SigningKey] | None = None,
) -> str:
    """Encode a signed cursor token."""

    return _encode_token(
        payload,
        prefix=CURSOR_PREFIX,
        type_tag="cursor",
        signing_keys=signing_keys,
    )


def decode_cursor(
    token: str,
    *,
    expected_env: str | None = None,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[_SigningKey] | None = None,
    now: int | None = None,
) -> CursorPayload:
    """Decode and validate a signed cursor token."""

    return _decode_token(
        token,
        prefix=CURSOR_PREFIX,
        type_tag="cursor",
        payload_type=CursorPayload,
        expected_env=expected_env,
        signing_keys=signing_keys,
        now=now,
    )


def encode_approval(
    payload: ApprovalPayload,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[_SigningKey] | None = None,
) -> str:
    """Encode a signed approval token."""

    return _encode_token(
        payload,
        prefix=APPROVAL_PREFIX,
        type_tag="approval",
        signing_keys=signing_keys,
    )


def decode_approval(
    token: str,
    *,
    expected_env: str | None = None,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[_SigningKey] | None = None,
    now: int | None = None,
) -> ApprovalPayload:
    """Decode and validate a signed approval token."""

    return _decode_token(
        token,
        prefix=APPROVAL_PREFIX,
        type_tag="approval",
        payload_type=ApprovalPayload,
        expected_env=expected_env,
        signing_keys=signing_keys,
        now=now,
    )


__all__ = [
    "APPROVAL_PREFIX",
    "CURSOR_PREFIX",
    "MAC_CONTEXT",
    "REF_PREFIX",
    "TOKEN_VERSION",
    "ApprovalPayload",
    "CursorPayload",
    "RefPayload",
    "canonical_cbor_deterministic",
    "decode_approval",
    "decode_cursor",
    "decode_ref",
    "encode_approval",
    "encode_cursor",
    "encode_ref",
]
