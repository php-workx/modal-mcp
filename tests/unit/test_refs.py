"""Unit tests for signed refs, cursors, and approval tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import sys
from collections import OrderedDict
from pathlib import Path

import cbor2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from modal_mcp.domain.refs import (
    ApprovalPayload,
    CursorPayload,
    RefPayload,
    decode_approval,
    decode_cursor,
    decode_ref,
    encode_approval,
    encode_cursor,
    encode_ref,
)

_MAC_CONTEXT = b"modal-mcp/v1"
_KEYS = (
    "kid1:000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f,"
    "kid2:202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f"
)


def _b64url(value: bytes) -> str:
    """Encode bytes using unpadded URL-safe base64."""

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _sign_token(
    prefix: str, type_tag: str, kid: str, key: bytes, payload: bytes
) -> str:
    """Build a signed token with the codec's MAC construction."""

    mac = hmac.new(
        key,
        _MAC_CONTEXT
        + b"\x00"
        + type_tag.encode("ascii")
        + b"\x00"
        + kid.encode("ascii")
        + b"\x00"
        + payload,
        hashlib.sha256,
    ).digest()
    return f"{prefix}.{_b64url(payload)}.{_b64url(mac)}"


def _key_from_env(index: int) -> tuple[str, bytes]:
    kid, raw_key = _KEYS.split(",")[index].split(":", 1)
    return kid, bytes.fromhex(raw_key)


def test_encode_and_decode_ref_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refs round-trip through the codec using the primary signing key."""

    monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", _KEYS)
    payload = RefPayload(
        id="mref1.app.abc",
        env="prod",
        ws="ws_123",
        exp=1_900_000_000,
    )

    token = encode_ref(payload)

    assert token.startswith("mref1.")
    assert decode_ref(token) == payload


def test_decode_cursor_accepts_secondary_signing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All configured signing keys must verify, not just the first one."""

    monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", _KEYS)
    kid, key = _key_from_env(1)
    payload = CursorPayload(
        cursor="mc1.cursor.opaque",
        env="prod",
        ws="ws_123",
        exp=1_900_000_000,
    )
    token = encode_cursor(payload, signing_keys=[(kid, key)])

    assert decode_cursor(token) == payload


def test_decode_rejects_cross_env_tokens_when_expected_env_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token minted for one environment must not validate against another."""

    monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", _KEYS)
    payload = RefPayload(
        id="mref1.app.abc",
        env="prod",
        ws="ws_123",
        exp=1_900_000_000,
    )
    token = encode_ref(payload)

    with pytest.raises(ValueError):
        decode_ref(token, expected_env="dev")


def test_decode_rejects_noncanonical_payload_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-canonical CBOR payload bytes must be rejected even if signed."""

    monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", _KEYS)
    kid, key = _key_from_env(0)
    payload = OrderedDict(
        [
            ("v", 1),
            ("exp", 1_900_000_000),
            ("ws", "ws_123"),
            ("env", "prod"),
            ("id", "mref1.app.abc"),
            ("kind", "ref"),
        ]
    )
    payload_bytes = cbor2.dumps(payload, canonical=False)
    token = _sign_token("mref1", "ref", kid, key, payload_bytes)

    with pytest.raises(ValueError):
        decode_ref(token)


def test_encode_and_decode_approval_sorts_target_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval targets are canonicalized into a stable sorted scope."""

    monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", _KEYS)
    payload = ApprovalPayload(
        tool_name="modal_stop_app",
        target_refs=("mref1.z", "mref1.a"),
        actor="alice",
        ws="ws_123",
        mcp_session_id="mcp-session-1",
        auth_session_id="auth-session-1",
        nonce="nonce-1",
        env="prod",
        exp=1_900_000_000,
    )

    token = encode_approval(payload)

    assert token.startswith("mappr1.")
    assert decode_approval(token) == ApprovalPayload(
        tool_name="modal_stop_app",
        target_refs=("mref1.a", "mref1.z"),
        actor="alice",
        ws="ws_123",
        mcp_session_id="mcp-session-1",
        auth_session_id="auth-session-1",
        nonce="nonce-1",
        env="prod",
        exp=1_900_000_000,
    )
