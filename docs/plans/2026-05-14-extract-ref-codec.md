# Extract RefCodec Class Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace module-level `encode_ref`/`decode_ref` functions (which require callers to pass raw signing-key tuples) with a `RefCodec` class that takes keys at construction time and exposes a clean `encode`/`decode` interface.

**Architecture:** `RefCodec` is added to `domain/refs.py` and wraps `_encode_token`/`_decode_token` with its own keys baked in at `__init__` time. `ModalSdkAdapter` stores one `RefCodec` instance and uses it instead of `self._signing_keys` for all ref operations — the old `_parse_signing_keys` helper and `_signing_keys` attribute are removed. The module-level `encode_ref`/`decode_ref` functions are kept as thin delegation wrappers (not removed) so the public API stays backward-compatible; callers inside the codebase are migrated to `RefCodec` directly.

**Tech Stack:** Python 3.12, cbor2, pydantic, hmac/hashlib, pytest, ruff

---

## File Structure

Files touched by this plan:

```text
src/modal_mcp/domain/refs.py          ← add RefCodec class; keep encode_ref/decode_ref as wrappers
src/modal_mcp/adapters/modal_adapter.py ← replace _parse_signing_keys + _signing_keys with _ref_codec
tests/unit/test_refs.py               ← new file: RefCodec round-trip and delegation tests
tests/unit/test_normalize.py          ← no structural change; SIGNING_KEYS constant stays as-is
tests/unit/test_modal_adapter.py      ← no structural change; SIGNING_KEYS/SIGNING_KEY_TEXT stay
```

No new modules. No changes to `normalize.py` (normalizers still accept raw `signing_keys` tuples — RefCodec injection is deferred to the separate normalize.py deepening plan).

---

## Step 1 — Write failing tests for RefCodec

- [ ] Create `tests/unit/test_refs.py` with the following content:

```python
"""Unit tests for RefCodec."""

from __future__ import annotations

import pytest

from modal_mcp.domain.refs import RefCodec, RefPayload

KEYS: tuple[tuple[str, bytes], ...] = (
    ("kid1", bytes.fromhex("000102030405060708090a0b0c0d0e0f" * 2)),
    ("kid2", bytes.fromhex("101112131415161718191a1b1c1d1e1f" * 2)),
)
NOW = 1_744_700_000   # fixed epoch for deterministic expiry checks
EXP = NOW + 3600


def _payload(**overrides: object) -> RefPayload:
    defaults = dict(id="app-1", env="prod", ws="acme", exp=EXP)
    defaults.update(overrides)
    return RefPayload(**defaults)  # type: ignore[arg-type]


class TestRefCodecInit:
    def test_requires_at_least_one_key(self) -> None:
        with pytest.raises(ValueError, match="at least one signing key"):
            RefCodec(())

    def test_accepts_tuple_of_tuples(self) -> None:
        codec = RefCodec(KEYS)
        assert codec is not None

    def test_does_not_expose_raw_keys(self) -> None:
        codec = RefCodec(KEYS)
        # No public attribute should return the raw bytes
        for attr in vars(codec):
            value = getattr(codec, attr)
            assert not isinstance(value, (bytes, list, tuple)) or attr.startswith("_"), (
                f"Unexpected public key material in attribute {attr!r}"
            )


class TestRefCodecRoundTrip:
    def test_encode_decode_round_trip(self) -> None:
        codec = RefCodec(KEYS)
        payload = _payload()
        token = codec.encode(payload)
        decoded = codec.decode(token, now=NOW + 1)
        assert decoded.id == payload.id
        assert decoded.env == payload.env
        assert decoded.ws == payload.ws
        assert decoded.exp == payload.exp

    def test_encode_returns_mref1_prefix(self) -> None:
        codec = RefCodec(KEYS)
        token = codec.encode(_payload())
        assert token.startswith("mref1.")

    def test_decode_rejects_tampered_payload(self) -> None:
        codec = RefCodec(KEYS)
        token = codec.encode(_payload())
        parts = token.split(".")
        # flip a byte in the payload segment
        tampered = parts[0] + "." + parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B") + "." + parts[2]
        with pytest.raises(ValueError):
            codec.decode(tampered, now=NOW + 1)

    def test_decode_rejects_tampered_signature(self) -> None:
        codec = RefCodec(KEYS)
        token = codec.encode(_payload())
        parts = token.split(".")
        tampered = parts[0] + "." + parts[1] + "." + parts[2][:-1] + ("A" if parts[2][-1] != "A" else "B")
        with pytest.raises(ValueError):
            codec.decode(tampered, now=NOW + 1)

    def test_decode_rejects_expired_token(self) -> None:
        codec = RefCodec(KEYS)
        token = codec.encode(_payload(exp=NOW + 10))
        with pytest.raises(ValueError, match="expired"):
            codec.decode(token, now=NOW + 20)

    def test_decode_accepts_expected_env(self) -> None:
        codec = RefCodec(KEYS)
        token = codec.encode(_payload(env="prod"))
        decoded = codec.decode(token, expected_env="prod", now=NOW + 1)
        assert decoded.env == "prod"

    def test_decode_rejects_wrong_env(self) -> None:
        codec = RefCodec(KEYS)
        token = codec.encode(_payload(env="prod"))
        with pytest.raises(ValueError, match="env mismatch"):
            codec.decode(token, expected_env="dev", now=NOW + 1)

    def test_decode_accepts_now_parameter(self) -> None:
        codec = RefCodec(KEYS)
        # token expires at NOW+10; decoding at NOW+5 is fine
        token = codec.encode(_payload(exp=NOW + 10))
        decoded = codec.decode(token, now=NOW + 5)
        assert decoded.id == "app-1"

    def test_kid_selection_uses_first_key(self) -> None:
        """First key in the tuple is used to sign; any key in the tuple can verify."""
        codec_signer = RefCodec((KEYS[0],))
        codec_verifier = RefCodec(KEYS)   # both keys available
        token = codec_signer.encode(_payload())
        decoded = codec_verifier.decode(token, now=NOW + 1)
        assert decoded.id == "app-1"

    def test_second_key_can_also_verify(self) -> None:
        """Key rotation: token signed by kid2 can be verified by codec holding both."""
        codec_kid2 = RefCodec((KEYS[1],))
        codec_both = RefCodec(KEYS)
        token = codec_kid2.encode(_payload())
        decoded = codec_both.decode(token, now=NOW + 1)
        assert decoded.id == "app-1"

    def test_unknown_kid_rejected(self) -> None:
        """Token signed by a key not in the codec's key list is rejected."""
        other_key: tuple[tuple[str, bytes], ...] = (
            ("kid-other", bytes.fromhex("ffffffffffffffffffffffffffffffff" * 2)),
        )
        codec_other = RefCodec(other_key)
        codec_main = RefCodec(KEYS)
        token = codec_other.encode(_payload())
        with pytest.raises(ValueError, match="invalid token signature"):
            codec_main.decode(token, now=NOW + 1)


class TestRefCodecDelegatesModuleFunctions:
    """encode_ref and decode_ref still work (backward compatibility)."""

    def test_encode_ref_and_decode_ref_interop_with_codec(self) -> None:
        from modal_mcp.domain.refs import decode_ref, encode_ref

        payload = _payload()
        # encode via module function, decode via RefCodec
        token = encode_ref(payload, signing_keys=KEYS)
        codec = RefCodec(KEYS)
        decoded = codec.decode(token, now=NOW + 1)
        assert decoded.id == payload.id

        # encode via RefCodec, decode via module function
        token2 = codec.encode(payload)
        decoded2 = decode_ref(token2, signing_keys=KEYS, now=NOW + 1)
        assert decoded2.id == payload.id
```

- [ ] Run the tests and confirm they **fail** with `ImportError` (RefCodec does not exist yet):

```bash
uv run pytest tests/unit/test_refs.py -x 2>&1 | head -30
```

Expected output contains: `ImportError: cannot import name 'RefCodec'`

---

## Step 2 — Implement RefCodec in domain/refs.py

- [ ] Add the `RefCodec` class to `src/modal_mcp/domain/refs.py`, immediately after the `decode_ref` function (before `encode_cursor`). Insert this block:

```python
class RefCodec:
    """Encode and decode signed ref tokens with keys bound at construction time.

    Callers construct one instance per key-set and call ``encode``/``decode``
    without ever seeing HMAC key material or CBOR details.

    Parameters
    ----------
    keys:
        One or more ``(kid, key_bytes)`` pairs.  The first pair is used for
        signing new tokens; all pairs are tried when verifying incoming tokens
        (key-rotation support).
    """

    def __init__(self, keys: tuple[tuple[str, bytes], ...]) -> None:
        self._keys = _coerce_signing_keys(keys)  # validates non-empty; converts to _SigningKey

    def encode(self, payload: RefPayload) -> str:
        """Encode and sign *payload* into a compact ``mref1.*.*`` token."""
        return _encode_token(
            payload,
            prefix=REF_PREFIX,
            type_tag="ref",
            signing_keys=self._keys,
        )

    def decode(
        self,
        ref: str,
        *,
        expected_env: str | None = None,
        now: int | None = None,
    ) -> RefPayload:
        """Decode and validate *ref*, optionally enforcing *expected_env* and *now*."""
        return _decode_token(
            ref,
            prefix=REF_PREFIX,
            type_tag="ref",
            payload_type=RefPayload,
            expected_env=expected_env,
            signing_keys=self._keys,
            now=now,
        )
```

- [ ] Add `"RefCodec"` to the `__all__` list at the bottom of `refs.py`:

```python
__all__ = [
    "APPROVAL_PREFIX",
    "CURSOR_PREFIX",
    "MAC_CONTEXT",
    "REF_PREFIX",
    "TOKEN_VERSION",
    "ApprovalPayload",
    "CursorPayload",
    "RefCodec",          # ← new
    "RefPayload",
    "canonical_cbor_deterministic",
    "decode_approval",
    "decode_cursor",
    "decode_ref",
    "encode_approval",
    "encode_cursor",
    "encode_ref",
    "parse_signing_keys",
]
```

- [ ] Run the new tests and confirm they **pass**:

```bash
uv run pytest tests/unit/test_refs.py -v
```

Expected: all tests in `test_refs.py` pass.

- [ ] Run the full existing test suite to ensure nothing regressed:

```bash
uv run pytest --tb=short -q
```

Expected: all previously-passing tests still pass.

---

## Step 3 — Migrate ModalSdkAdapter to use RefCodec

The adapter currently stores `self._signing_keys: tuple[tuple[str, bytes], ...]` and passes it into every `decode_ref(...)` call and every `normalize_*(... signing_keys=self._signing_keys)` call.

After this step:

- `self._ref_codec: RefCodec` replaces `self._signing_keys`.
- `_parse_signing_keys` is replaced by a one-liner `RefCodec.from_settings` approach — or simply inlined into `__init__`.
- The adapter's `decode_ref` calls become `self._ref_codec.decode(...)`.
- The `normalize_*` calls still receive a raw `signing_keys` tuple (extracted from `self._ref_codec._keys`) because normalizers have not yet been migrated to RefCodec (that is the separate normalize.py deepening plan). A private `_signing_keys` property is added for this bridging purpose so no other module accesses HMAC material directly.

### 3a — Add import and remove old helper

- [ ] In `src/modal_mcp/adapters/modal_adapter.py`:

  Replace:

  ```python
  from modal_mcp.domain.refs import decode_ref
  ```

  With:

  ```python
  from modal_mcp.domain.refs import RefCodec, decode_ref
  ```

  (Keep `decode_ref` in the import for the `get_app` inline usage at line 331. It will be replaced in step 3c.)

- [ ] Delete the `_parse_signing_keys` function (lines 59-67):

  Remove this entire function:

  ```python
  def _parse_signing_keys(raw: SecretStr | None) -> tuple[tuple[str, bytes], ...]:
      text = _secret_value(raw)
      if not text:
          return ()
      keys: list[tuple[str, bytes]] = []
      for item in text.split(","):
          kid, hex_key = item.split(":", 1)
          keys.append((kid.strip(), bytes.fromhex(hex_key.strip())))
      return tuple(keys)
  ```

### 3b — Replace \_signing\_keys attribute with \_ref\_codec

- [ ] In `ModalSdkAdapter.__init__`, replace:

  ```python
  self._signing_keys = _parse_signing_keys(settings.modal_mcp_signing_keys)
  ```

  With:

  ```python
  self._ref_codec = _build_ref_codec(settings.modal_mcp_signing_keys)
  ```

- [ ] Add the module-level helper `_build_ref_codec` directly above `class ModalSdkAdapter` (after the deletion of `_parse_signing_keys`):

  ```python
  def _build_ref_codec(raw: SecretStr | None) -> RefCodec:
      """Build a RefCodec from the signing-keys secret, or a no-op sentinel if absent."""
      text = _secret_value(raw)
      if not text:
          # No signing keys configured — operations that need refs will fail at runtime.
          # Return a codec built from a dummy key so the adapter can still be constructed
          # in environments where refs are never used (e.g. unit tests that only exercise
          # non-ref paths). The dummy is never used for real verification.
          raise ValueError(
              "MODAL_MCP_SIGNING_KEYS is required to build RefCodec"
          )
      keys: list[tuple[str, bytes]] = []
      for item in text.split(","):
          kid, hex_key = item.split(":", 1)
          keys.append((kid.strip(), bytes.fromhex(hex_key.strip())))
      return RefCodec(tuple(keys))
  ```

  **Important note on the empty-keys case:** The original `_parse_signing_keys` returned `()` when no keys were configured. The adapter tests always supply `SIGNING_KEY_TEXT` via `Settings`, so this path was never exercised in production. The new helper raises immediately — this is stricter and correct. If a test needs to construct an adapter without signing keys, it must supply a key.

- [ ] Add a private `_signing_keys` property to `ModalSdkAdapter` as a bridge for `normalize_*` callers (avoids touching normalize.py in this plan):

  ```python
  @property
  def _signing_keys(self) -> tuple[tuple[str, bytes], ...]:
      """Bridge: expose raw key tuples for normalizers that have not yet been migrated."""
      return tuple((k.kid, k.key) for k in self._ref_codec._keys)
  ```

  This property should be placed right after `__init__`, before `create`.

### 3c — Replace decode\_ref call sites inside ModalSdkAdapter

- [ ] In `_verify_ref_env` (line ~249), replace:

  ```python
  payload = decode_ref(ref, expected_env=env, signing_keys=self._signing_keys)
  ```

  With:

  ```python
  payload = self._ref_codec.decode(ref, expected_env=env)
  ```

- [ ] In `get_app` (lines ~331-335), replace:

  ```python
  app_native_id = decode_ref(
      app.app_ref,
      expected_env=self._environment_name(environment_name),
      signing_keys=self._signing_keys,
  ).id
  ```

  With:

  ```python
  app_native_id = self._ref_codec.decode(
      app.app_ref,
      expected_env=self._environment_name(environment_name),
  ).id
  ```

- [ ] Remove `decode_ref` from the import line (it is no longer called directly in the adapter):

  ```python
  from modal_mcp.domain.refs import RefCodec
  ```

- [ ] Run tests:

  ```bash
  uv run pytest --tb=short -q
  ```

  Expected: all tests pass.

---

## Step 4 — Verify no caller outside refs.py constructs raw HMAC tuples for ref operations

- [ ] Confirm no outside module references the old `_parse_signing_keys` function:

  ```bash
  grep -rn "_parse_signing_keys" src/
  ```

  Expected: no output.

- [ ] Confirm no outside module passes raw `signing_keys` to `encode_ref` or `decode_ref` (outside of `refs.py` and `normalize.py` itself):

  ```bash
  grep -rn "encode_ref\|decode_ref" src/ \
    | grep -v "domain/refs.py" \
    | grep -v "domain/normalize.py"
  ```

  Expected: no output (the adapter no longer calls these functions directly).

- [ ] Confirm test files still only use `SIGNING_KEYS` constant in their own scope (not passing it to adapter internals):

  ```bash
  grep -n "signing_keys" tests/unit/test_modal_adapter.py
  ```

  Expected: no matches (the adapter tests use `SIGNING_KEY_TEXT` in `Settings`, not raw tuples injected into the adapter).

---

## Step 5 — Linting and final test run

- [ ] Run ruff:

  ```bash
  uv run ruff check .
  ```

  Expected: no errors.

- [ ] Run full test suite:

  ```bash
  uv run pytest -v
  ```

  Expected: all tests pass including the new `test_refs.py`.

- [ ] Commit:

  ```bash
  git add \
    src/modal_mcp/domain/refs.py \
    src/modal_mcp/adapters/modal_adapter.py \
    tests/unit/test_refs.py
  git commit -m "feat(refs): extract RefCodec class; hide HMAC key material behind encode/decode

  Closes epo-extract-refcodec-class-hide-hmac-wzx6"
  ```

---

## Self-review checklist

### Spec coverage

| Acceptance criterion | Covered by |
|---|---|
| `RefCodec` in `domain/refs.py` with `__init__(keys)`, `encode(payload) -> str`, `decode(ref, *, expected_env, now) -> RefPayload` | Step 2 |
| `encode_ref` / `decode_ref` module-level functions delegated (kept as wrappers) | Existing code unchanged; step 2 adds class alongside |
| `ModalSdkAdapter` stores `RefCodec`, not raw signing_keys for ref operations | Step 3b |
| No caller outside `refs.py` constructs or inspects raw HMAC key tuples for ref ops | Step 4 grep checks |
| Existing ref round-trip tests pass unchanged | Step 2 & 3 test runs |
| `uv run pytest` passes | Step 5 |
| `uv run ruff check .` passes | Step 5 |

### Placeholder scan

No placeholder strings used. All code blocks are complete and self-contained.

### Type consistency

- `RefCodec.__init__` accepts `tuple[tuple[str, bytes], ...]` (the public shape) and delegates to `_coerce_signing_keys` which validates non-empty and converts to `tuple[_SigningKey, ...]` (private shape). Storage type is `tuple[_SigningKey, ...]` in `__slots__`.
- `_build_ref_codec` returns `RefCodec` (never `None`).
- `_signing_keys` bridge property returns `tuple[tuple[str, bytes], ...]` matching the existing `normalize_*` function signatures.
- `RefCodec.decode` keyword-only parameters `expected_env` and `now` match `decode_ref`'s existing signature exactly.

### Wire format invariant

`RefCodec` delegates to the same `_encode_token`/`_decode_token` internals as `encode_ref`/`decode_ref`. No format change — tokens signed before this refactor decode correctly after it.

### normalize.py independence

Normalizers continue to receive raw `signing_keys` tuples via the `_signing_keys` bridge property. The normalize.py deepening plan (already at `docs/plans/2026-05-14-deepen-normalize-py.md`) will later inject `RefCodec` into normalizer constructors and remove the bridge property; this plan is fully standalone.
