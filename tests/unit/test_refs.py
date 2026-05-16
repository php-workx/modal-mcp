"""Unit tests for signed refs, cursors, and approval tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections import OrderedDict

import cbor2
import pytest

from modal_mcp.domain.refs import (
    ApprovalPayload,
    CursorPayload,
    RefCodec,
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


# ---------------------------------------------------------------------------
# RefCodec tests
# ---------------------------------------------------------------------------

_CODEC_KEYS: tuple[tuple[str, bytes], ...] = (
    ("kid1", bytes.fromhex("000102030405060708090a0b0c0d0e0f" * 2)),
    ("kid2", bytes.fromhex("101112131415161718191a1b1c1d1e1f" * 2)),
)
_NOW = 1_744_700_000  # fixed epoch for deterministic expiry checks
_EXP = _NOW + 3600


def _ref_payload(**overrides: object) -> RefPayload:
    defaults = dict(id="app-1", env="prod", ws="acme", exp=_EXP)
    defaults.update(overrides)
    return RefPayload(**defaults)  # type: ignore[arg-type]


class TestRefCodecInit:
    def test_requires_at_least_one_key(self) -> None:
        with pytest.raises(ValueError, match="at least one signing key"):
            RefCodec(())

    def test_accepts_tuple_of_tuples(self) -> None:
        codec = RefCodec(_CODEC_KEYS)
        assert codec is not None

    def test_does_not_expose_raw_keys(self) -> None:
        codec = RefCodec(_CODEC_KEYS)
        # No public attribute should return the raw bytes
        for attr in vars(codec):
            value = getattr(codec, attr)
            is_raw_material = isinstance(value, (bytes, list, tuple))
            assert not is_raw_material or attr.startswith("_"), (
                f"Unexpected public key material in attribute {attr!r}"
            )


class TestRefCodecRoundTrip:
    def test_encode_decode_round_trip(self) -> None:
        codec = RefCodec(_CODEC_KEYS)
        payload = _ref_payload()
        token = codec.encode(payload)
        decoded = codec.decode(token, now=_NOW + 1)
        assert decoded.id == payload.id
        assert decoded.env == payload.env
        assert decoded.ws == payload.ws
        assert decoded.exp == payload.exp

    def test_encode_returns_mref1_prefix(self) -> None:
        codec = RefCodec(_CODEC_KEYS)
        token = codec.encode(_ref_payload())
        assert token.startswith("mref1.")

    def test_decode_rejects_tampered_payload(self) -> None:
        codec = RefCodec(_CODEC_KEYS)
        token = codec.encode(_ref_payload())
        parts = token.split(".")
        # flip a byte in the payload segment
        flip = "A" if parts[1][-1] != "A" else "B"
        tampered = parts[0] + "." + parts[1][:-1] + flip + "." + parts[2]
        with pytest.raises(ValueError):
            codec.decode(tampered, now=_NOW + 1)

    def test_decode_rejects_tampered_signature(self) -> None:
        codec = RefCodec(_CODEC_KEYS)
        token = codec.encode(_ref_payload())
        parts = token.split(".")
        flip = "A" if parts[2][-1] != "A" else "B"
        tampered = parts[0] + "." + parts[1] + "." + parts[2][:-1] + flip
        with pytest.raises(ValueError):
            codec.decode(tampered, now=_NOW + 1)

    def test_decode_rejects_expired_token(self) -> None:
        codec = RefCodec(_CODEC_KEYS)
        token = codec.encode(_ref_payload(exp=_NOW + 10))
        with pytest.raises(ValueError, match="expired"):
            codec.decode(token, now=_NOW + 20)

    def test_decode_accepts_expected_env(self) -> None:
        codec = RefCodec(_CODEC_KEYS)
        token = codec.encode(_ref_payload(env="prod"))
        decoded = codec.decode(token, expected_env="prod", now=_NOW + 1)
        assert decoded.env == "prod"

    def test_decode_rejects_wrong_env(self) -> None:
        codec = RefCodec(_CODEC_KEYS)
        token = codec.encode(_ref_payload(env="prod"))
        with pytest.raises(ValueError, match="env mismatch"):
            codec.decode(token, expected_env="dev", now=_NOW + 1)

    def test_decode_accepts_now_parameter(self) -> None:
        codec = RefCodec(_CODEC_KEYS)
        # token expires at _NOW+10; decoding at _NOW+5 is fine
        token = codec.encode(_ref_payload(exp=_NOW + 10))
        decoded = codec.decode(token, now=_NOW + 5)
        assert decoded.id == "app-1"

    def test_kid_selection_uses_first_key(self) -> None:
        """First key in the tuple is used to sign; any key in the tuple can verify."""
        codec_signer = RefCodec((_CODEC_KEYS[0],))
        codec_verifier = RefCodec(_CODEC_KEYS)  # both keys available
        token = codec_signer.encode(_ref_payload())
        decoded = codec_verifier.decode(token, now=_NOW + 1)
        assert decoded.id == "app-1"

    def test_second_key_can_also_verify(self) -> None:
        """Key rotation: token signed by kid2 can be verified by codec holding both."""
        codec_kid2 = RefCodec((_CODEC_KEYS[1],))
        codec_both = RefCodec(_CODEC_KEYS)
        token = codec_kid2.encode(_ref_payload())
        decoded = codec_both.decode(token, now=_NOW + 1)
        assert decoded.id == "app-1"

    def test_unknown_kid_rejected(self) -> None:
        """Token signed by a key not in the codec's key list is rejected."""
        other_key: tuple[tuple[str, bytes], ...] = (
            ("kid-other", bytes.fromhex("ffffffffffffffffffffffffffffffff" * 2)),
        )
        codec_other = RefCodec(other_key)
        codec_main = RefCodec(_CODEC_KEYS)
        token = codec_other.encode(_ref_payload())
        with pytest.raises(ValueError, match="invalid token signature"):
            codec_main.decode(token, now=_NOW + 1)


class TestRefCodecDelegatesModuleFunctions:
    """encode_ref and decode_ref still work (backward compatibility)."""

    def test_encode_ref_and_decode_ref_interop_with_codec(self) -> None:
        payload = _ref_payload()
        # encode via module function, decode via RefCodec
        token = encode_ref(payload, signing_keys=_CODEC_KEYS)
        codec = RefCodec(_CODEC_KEYS)
        decoded = codec.decode(token, now=_NOW + 1)
        assert decoded.id == payload.id

        # encode via RefCodec, decode via module function
        token2 = codec.encode(payload)
        decoded2 = decode_ref(token2, signing_keys=_CODEC_KEYS, now=_NOW + 1)
        assert decoded2.id == payload.id
