# Split ModalSdkAdapter Bootstrap Into CredentialSource + ModalClient + Adapter Assembly

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose `ModalSdkAdapter.create(settings)` — which currently mixes credential resolution, Modal SDK client construction, and normalizer assembly — into three explicit phases owned by separate modules. Bootstrap failures surface **before** the FastMCP lifespan starts, carry **provenance** ("loaded from `MODAL_TOKEN_ID` env var" vs "loaded from `~/.modal.toml` at profile `foo`"), and tests can inject a fake `ModalClient` without mocking credential resolution.

**Architecture:**

- `src/modal_mcp/adapters/credentials.py` — NEW. `ModalCredentials` frozen dataclass + `CredentialSource.resolve(settings) -> ModalCredentials`. Pure resolution; no I/O against Modal.
- `src/modal_mcp/adapters/modal_adapter.py` — `ModalClientFactory.from_credentials(creds) -> ModalClient` is **absorbed into the existing `ModalRpcClient`** as a `ModalRpcClient.from_credentials` classmethod. Rationale: `ModalRpcClient` already owns the client lifecycle (close, reconnect); putting construction next to it is the cohesive shape. No new `client.py` module.
- `ModalSdkAdapter.create(settings, *, client, ref_codec)` becomes pure assembly: construct 8 normalizers, store rpc + codec, return. Signature changes — `client_factory` parameter is removed (factory is now a `ModalRpcClient` constructor concern).
- `src/modal_mcp/server.py` — `_default_adapter_factory` is rewritten to orchestrate the three phases in order: resolve credentials → build `ModalRpcClient` (with auth ping) → assemble adapter. Bootstrap failures surface in `create_mcp` / `create_asgi_app` callers, not inside the FastMCP lifespan.
- `src/modal_mcp/doctor.py` — `CredentialProbeResult` already carries `source`; extend it with `profile: str | None` and update messages to read "loaded from `MODAL_TOKEN_ID` env var" or "loaded from `~/.modal.toml` at profile `foo`".

**Tech Stack:** Python 3.12, pydantic v2, pytest + pytest-asyncio, ruff.

---

## File Structure

| Path | Change |
|---|---|
| `src/modal_mcp/adapters/credentials.py` | **NEW**: `ModalCredentials` dataclass + `CredentialError` + `CredentialSource.resolve` |
| `src/modal_mcp/adapters/modal_adapter.py` | Add `ModalRpcClient.from_credentials` classmethod; shrink `ModalSdkAdapter.create` to require `client` + `ref_codec`; drop `_build_ref_codec`, `_create_modal_client`, `client_factory` parameter |
| `src/modal_mcp/server.py` | Rewrite `_default_adapter_factory` to orchestrate bootstrap; surface failures before lifespan |
| `src/modal_mcp/doctor.py` | Extend `CredentialProbeResult` with `profile`; rewrite messages to include provenance string |
| `tests/unit/test_credentials.py` | **NEW**: env/toml/injected provenance + failure modes |
| `tests/unit/test_modal_adapter.py` | Shrink: drop tests that exercised credential resolution; keep normalization dispatch tests; update `create()` callsites to pass `ref_codec` |
| `tests/unit/test_doctor.py` | Update assertions for new provenance message shape |
| `tests/unit/test_server_run.py` | Add coverage for bootstrap-failure-before-lifespan surface |

No new module under `src/modal_mcp/adapters/client.py` — the factory is absorbed into `ModalRpcClient` (see Architecture rationale above).

---

## Step 1 — RED: write failing tests for `CredentialSource` and `ModalCredentials`

Source-driven development boundary: before implementing, capture exactly the provenance contract through tests.

- [ ] **1.1** Create `tests/unit/test_credentials.py`:

```python
"""Unit tests for CredentialSource and ModalCredentials."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from modal_mcp.adapters.credentials import (
    CredentialError,
    CredentialSource,
    ModalCredentials,
)
from modal_mcp.config import Settings

SIGNING_KEY_TEXT = "kid1:" + "a" * 64


def _settings(
    tmp_path: Path,
    *,
    token_id: str | None = None,
    token_secret: str | None = None,
    modal_config_text: str | None = "[default]\n",
    profile: str | None = None,
) -> Settings:
    """Build minimal Settings with optional tokens / modal.toml / profile."""
    config_path = tmp_path / "modal.toml"
    if modal_config_text is not None:
        config_path.write_text(modal_config_text, encoding="utf-8")
    kwargs: dict[str, object] = {
        "modal_config_path": config_path,
        "modal_mcp_allowed_origins": ("http://127.0.0.1:8765",),
        "modal_mcp_signing_keys": SecretStr(SIGNING_KEY_TEXT),
    }
    if token_id is not None:
        kwargs["modal_token_id"] = SecretStr(token_id)
    if token_secret is not None:
        kwargs["modal_token_secret"] = SecretStr(token_secret)
    if profile is not None:
        kwargs["modal_profile"] = profile  # Settings gains this field in Step 2.2
    return Settings(**kwargs)


def _creds(**overrides: object) -> ModalCredentials:
    base = dict(
        token_id=SecretStr("ak-1"),
        token_secret=SecretStr("as-1"),
        source="env",
        profile=None,
    )
    base.update(overrides)
    return ModalCredentials(**base)  # type: ignore[arg-type]


class TestModalCredentials:
    def test_is_frozen(self) -> None:
        creds = _creds()
        with pytest.raises(Exception):  # FrozenInstanceError
            creds.source = "toml"  # type: ignore[misc]

    def test_repr_does_not_leak_secret(self) -> None:
        creds = _creds(
            token_id=SecretStr("ak-secret-id"),
            token_secret=SecretStr("as-secret-value"),
        )
        text = repr(creds)
        assert "ak-secret-id" not in text
        assert "as-secret-value" not in text

    def test_describe_env_source(self) -> None:
        assert _creds(source="env").describe() == "loaded from MODAL_TOKEN_ID env var"

    def test_describe_toml_source_includes_profile(self, tmp_path: Path) -> None:
        creds = _creds(
            source="toml", profile="staging", config_path=tmp_path / "modal.toml"
        )
        assert creds.describe() == (
            f"loaded from {tmp_path / 'modal.toml'} at profile 'staging'"
        )

    def test_describe_injected_source(self) -> None:
        assert _creds(source="injected").describe() == "injected by caller (test/fake)"


class TestCredentialSourceResolveEnv:
    def test_env_pair_takes_priority(self, tmp_path: Path) -> None:
        settings = _settings(
            tmp_path, token_id="ak-env", token_secret="as-env"
        )
        creds = CredentialSource.resolve(settings)
        assert creds.source == "env"
        assert creds.profile is None
        assert creds.token_id.get_secret_value() == "ak-env"
        assert creds.token_secret.get_secret_value() == "as-env"


class TestCredentialSourceResolveToml:
    def test_toml_fallback_when_no_env_tokens(self, tmp_path: Path) -> None:
        toml_text = (
            "[default]\n"
            'token_id = "ak-toml"\n'
            'token_secret = "as-toml"\n'
        )
        settings = _settings(tmp_path, modal_config_text=toml_text)
        creds = CredentialSource.resolve(settings)
        assert creds.source == "toml"
        assert creds.profile == "default"
        assert creds.token_id.get_secret_value() == "ak-toml"
        assert creds.token_secret.get_secret_value() == "as-toml"

    def test_toml_named_profile(self, tmp_path: Path) -> None:
        toml_text = (
            "[default]\n"
            'token_id = "ak-default"\n'
            'token_secret = "as-default"\n'
            "[staging]\n"
            'token_id = "ak-staging"\n'
            'token_secret = "as-staging"\n'
        )
        settings = _settings(
            tmp_path, modal_config_text=toml_text, profile="staging"
        )
        creds = CredentialSource.resolve(settings)
        assert creds.source == "toml"
        assert creds.profile == "staging"
        assert creds.token_id.get_secret_value() == "ak-staging"


class TestCredentialSourceFailureModes:
    def test_missing_toml_and_no_env_raises(self, tmp_path: Path) -> None:
        # Settings construction requires modal.toml to exist; build with placeholder,
        # then delete to test the post-Settings resolution path.
        settings = _settings(tmp_path)
        settings.modal_config_path.unlink()
        with pytest.raises(CredentialError, match="no Modal credentials"):
            CredentialSource.resolve(settings)

    def test_malformed_toml_raises(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, modal_config_text="this is not [valid toml")
        with pytest.raises(CredentialError, match="could not parse"):
            CredentialSource.resolve(settings)

    def test_toml_missing_token_id_raises(self, tmp_path: Path) -> None:
        settings = _settings(
            tmp_path, modal_config_text='[default]\ntoken_secret = "as-only"\n'
        )
        with pytest.raises(CredentialError, match="token_id"):
            CredentialSource.resolve(settings)

    def test_toml_profile_not_found_raises(self, tmp_path: Path) -> None:
        toml_text = '[default]\ntoken_id = "x"\ntoken_secret = "y"\n'
        settings = _settings(
            tmp_path, modal_config_text=toml_text, profile="missing"
        )
        with pytest.raises(CredentialError, match="profile 'missing'"):
            CredentialSource.resolve(settings)


class TestCredentialSourceInjected:
    def test_inject_bypasses_resolution(self) -> None:
        injected = ModalCredentials(
            token_id=SecretStr("ak-inject"),
            token_secret=SecretStr("as-inject"),
            source="injected",
            profile=None,
        )
        assert injected.source == "injected"
        assert injected.describe() == "injected by caller (test/fake)"
```

- [ ] **1.2** Run the new tests; confirm `ImportError`:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_credentials.py -x 2>&1 | tail -10
```

Expected: `ImportError: cannot import name 'CredentialError'` (or similar).

---

## Step 2 — GREEN: implement `credentials.py` + add `Settings.modal_profile`

- [ ] **2.1** Create `src/modal_mcp/adapters/credentials.py`:

```python
"""Modal credential resolution with explicit provenance.

This module owns the *resolution* phase of bootstrap.  It is pure:
no Modal SDK import, no network I/O.  Failures raise CredentialError
with messages that name the source ('env var X', 'TOML file Y at profile Z').
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from modal_mcp.config import Settings

CredentialSourceKind = Literal["env", "toml", "injected"]


class CredentialError(ValueError):
    """Raised when Modal credentials cannot be resolved from any source."""


@dataclass(frozen=True, slots=True)
class ModalCredentials:
    """Resolved Modal credentials with explicit provenance.

    Attributes
    ----------
    token_id:
        Modal API token id, wrapped in :class:`SecretStr` so accidental
        logging does not leak the value.
    token_secret:
        Modal API token secret, wrapped in :class:`SecretStr`.
    source:
        One of ``"env"``, ``"toml"``, or ``"injected"``.  Drives the
        operator-facing ``describe()`` message used by ``doctor`` and
        bootstrap failure reporting.
    profile:
        TOML profile name when ``source == "toml"``; ``None`` otherwise.
    config_path:
        Absolute path to the modal.toml file when ``source == "toml"``;
        ``None`` otherwise.
    """

    token_id: SecretStr
    token_secret: SecretStr
    source: CredentialSourceKind
    profile: str | None = None
    config_path: Path | None = field(default=None)

    def describe(self) -> str:
        """Return an operator-facing provenance string (no secret material)."""
        if self.source == "env":
            return "loaded from MODAL_TOKEN_ID env var"
        if self.source == "toml":
            path = self.config_path or Path("~/.modal.toml")
            profile = self.profile or "default"
            return f"loaded from {path} at profile '{profile}'"
        return "injected by caller (test/fake)"


class CredentialSource:
    """Resolve Modal credentials from Settings with explicit provenance.

    The class is intentionally a namespace (single classmethod) rather than
    an instance: there is no resolver state to thread, and the call site
    reads better as ``CredentialSource.resolve(settings)``.
    """

    @classmethod
    def resolve(cls, settings: Settings) -> ModalCredentials:
        """Resolve credentials with explicit provenance.

        Priority: (1) ``settings.modal_token_id`` + ``modal_token_secret``
        (includes file-backed ``*_FILE`` per ``Settings._load_file_backed_secrets``)
        → source ``"env"``.  (2) ``settings.modal_config_path`` (default
        ``~/.modal.toml``) with ``settings.modal_profile`` (default ``"default"``)
        → source ``"toml"``.  Raises :class:`CredentialError` when neither yields
        a complete token pair.
        """
        if settings.modal_token_id is not None and settings.modal_token_secret is not None:
            return ModalCredentials(
                token_id=settings.modal_token_id,
                token_secret=settings.modal_token_secret,
                source="env",
                profile=None,
            )

        config_path = settings.modal_config_path.expanduser()
        profile = settings.modal_profile or "default"
        if not config_path.is_file():
            msg = (
                f"no Modal credentials available: env vars unset and "
                f"{config_path} does not exist"
            )
            raise CredentialError(msg)

        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            msg = f"could not parse Modal config file {config_path}: {exc}"
            raise CredentialError(msg) from exc

        section = data.get(profile)
        if section is None:
            msg = (
                f"Modal config file {config_path} has no profile '{profile}'; "
                f"available profiles: {sorted(data.keys())!r}"
            )
            raise CredentialError(msg)

        token_id = section.get("token_id")
        token_secret = section.get("token_secret")
        if not token_id or not token_secret:
            missing = [
                name
                for name, value in (("token_id", token_id), ("token_secret", token_secret))
                if not value
            ]
            msg = (
                f"Modal config file {config_path} profile '{profile}' is "
                f"missing required keys: {missing!r}"
            )
            raise CredentialError(msg)

        return ModalCredentials(
            token_id=SecretStr(str(token_id)),
            token_secret=SecretStr(str(token_secret)),
            source="toml",
            profile=profile,
            config_path=config_path,
        )


__all__ = [
    "CredentialError",
    "CredentialSource",
    "ModalCredentials",
    "CredentialSourceKind",
]
```

- [ ] **2.2** Add `modal_profile` field to `Settings` in `src/modal_mcp/config.py`. Insert directly after `modal_environment` (around line 119):

```python
    modal_profile: str | None = Field(
        default=None,
        validation_alias="MODAL_PROFILE",
    )
```

- [ ] **2.3** Run the failing tests; confirm they pass:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_credentials.py -v 2>&1 | tail -30
```

Expected: all 11 tests PASS.

- [ ] **2.4** Run the full suite; confirm no regressions:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest -q 2>&1 | tail -10
```

Expected: pre-existing test count + 11 new tests, all pass.

---

## Step 3 — RED: failing test for `ModalRpcClient.from_credentials`

The Modal client factory absorbs into `ModalRpcClient` so client lifecycle (construction + close + reconnect) stays cohesive in one class.

- [ ] **3.1** Append to `tests/unit/test_modal_adapter.py` (directly after the existing `test_modal_rpc_client_call_raises_after_two_failures` test):

```python
@pytest.mark.asyncio
async def test_modal_rpc_client_from_credentials_uses_from_credentials_aio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ModalRpcClient.from_credentials calls modal.Client.from_credentials.aio."""
    from modal_mcp.adapters.credentials import ModalCredentials
    from modal_mcp.adapters.modal_adapter import ModalRpcClient

    import modal

    factory = FakeModalFactory(FakeClient(FakeStub()))
    monkeypatch.setattr(modal.Client, "from_credentials", factory)

    creds = ModalCredentials(
        token_id=SecretStr("ak-1"),
        token_secret=SecretStr("as-1"),
        source="env",
        profile=None,
    )
    rpc = await ModalRpcClient.from_credentials(creds)

    # The Modal SDK was called with the credential tuple
    assert factory.aio_calls == [("ak-1", "as-1")]
    # The returned rpc is wired to the fake client
    assert rpc.request("Empty") is not None


@pytest.mark.asyncio
async def test_modal_rpc_client_from_credentials_validates_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ModalRpcClient.from_credentials runs a WorkspaceNameLookup ping."""
    from modal_mcp.adapters.credentials import ModalCredentials
    from modal_mcp.adapters.modal_adapter import ModalRpcClient

    import modal

    stub = FakeStub()
    monkeypatch.setattr(
        modal.Client,
        "from_credentials",
        FakeModalFactory(FakeClient(stub)),
    )

    creds = ModalCredentials(
        token_id=SecretStr("ak-1"),
        token_secret=SecretStr("as-1"),
        source="env",
        profile=None,
    )
    await ModalRpcClient.from_credentials(creds)

    # Auth probe ran exactly once on construction
    assert len(stub.requests) == 1
```

- [ ] **3.2** Run; confirm `AttributeError` (classmethod does not exist yet):

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_modal_adapter.py -k from_credentials -x 2>&1 | tail -15
```

Expected: `AttributeError: type object 'ModalRpcClient' has no attribute 'from_credentials'`.

---

## Step 4 — GREEN: add `ModalRpcClient.from_credentials` + remove `_create_modal_client`

- [ ] **4.1** Open `src/modal_mcp/adapters/modal_adapter.py`. Add `from_credentials` classmethod to `ModalRpcClient`, immediately after `__init__` (around line 127):

```python
    @classmethod
    async def from_credentials(
        cls,
        creds: ModalCredentials,
        *,
        client_factory: ClientFactory | None = None,
    ) -> ModalRpcClient:
        """Construct a ModalRpcClient from resolved credentials.

        Instantiates the Modal SDK client via ``modal.Client.from_credentials.aio``
        and runs a cheap ``WorkspaceNameLookup`` auth probe before returning.
        Auth failures surface as :class:`ModalAdapterError(UPSTREAM_ERROR)`.
        """
        try:
            import modal
        except ImportError as exc:  # pragma: no cover - dependency guard
            msg = "Modal SDK is not installed"
            raise ModalAdapterError(ErrorCode.INTERNAL_DRIFT, msg) from exc

        client = await modal.Client.from_credentials.aio(
            creds.token_id.get_secret_value(),
            creds.token_secret.get_secret_value(),
        )
        rpc = cls(client, client_factory=client_factory)
        try:
            rpc.call("WorkspaceNameLookup", rpc.request("Empty"))
        except ModalAdapterError:
            raise
        except Exception as exc:
            msg = (
                f"Modal auth probe failed for credentials ({creds.describe()}): "
                f"{type(exc).__name__}"
            )
            raise ModalAdapterError(
                ErrorCode.UPSTREAM_ERROR, msg, retryable=False
            ) from exc
        return rpc
```

- [ ] **4.2** Add the import at the top of `modal_adapter.py` (just after `from modal_mcp.domain.refs import ...`):

```python
from modal_mcp.adapters.credentials import ModalCredentials
```

- [ ] **4.3** Delete `ModalSdkAdapter._create_modal_client` (lines ~246-257). It is replaced by `ModalRpcClient.from_credentials`.

- [ ] **4.4** Run the new tests; confirm PASS:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_modal_adapter.py -k from_credentials -v 2>&1 | tail -15
```

Expected: both tests PASS.

---

## Step 5 — RED: failing test for `ModalSdkAdapter.create` requiring `client` + `ref_codec`

`ModalSdkAdapter.create` becomes pure assembly. No more credential resolution, no more client construction, no more `_build_ref_codec`.

- [ ] **5.1** Append to `tests/unit/test_modal_adapter.py`:

```python
@pytest.mark.asyncio
async def test_create_requires_explicit_ref_codec(
    modal_config_path: Path,
) -> None:
    """ModalSdkAdapter.create accepts a pre-built RefCodec; no fallback parsing."""
    from modal_mcp.adapters.modal_adapter import ModalRpcClient
    from modal_mcp.domain.refs import RefCodec

    ref_codec = RefCodec(SIGNING_KEYS)
    client = FakeClient(FakeStub())
    rpc = ModalRpcClient(client)

    adapter = await ModalSdkAdapter.create(
        settings(modal_config_path),
        client=client,
        ref_codec=ref_codec,
    )

    # The adapter exposes whoami via its assembled normalizer
    assert adapter.whoami().name == "acme"
    # Internal: ref_codec is stored as-is (not rebuilt from settings)
    assert adapter._ref_codec is ref_codec


@pytest.mark.asyncio
async def test_create_no_longer_accepts_client_factory(
    modal_config_path: Path,
) -> None:
    """ModalSdkAdapter.create no longer accepts client_factory (moved to RPC)."""
    from modal_mcp.domain.refs import RefCodec

    with pytest.raises(TypeError, match="client_factory"):
        await ModalSdkAdapter.create(
            settings(modal_config_path),
            client=FakeClient(FakeStub()),
            ref_codec=RefCodec(SIGNING_KEYS),
            client_factory=lambda: FakeClient(FakeStub()),  # type: ignore[call-arg]
        )
```

- [ ] **5.2** Run; confirm tests fail (current `create` does not accept `ref_codec`):

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_modal_adapter.py -k "create_requires_explicit_ref_codec or create_no_longer_accepts_client_factory" -x 2>&1 | tail -15
```

Expected: TypeError / signature mismatch.

---

## Step 6 — GREEN: shrink `ModalSdkAdapter.create` to pure assembly

- [ ] **6.1** In `src/modal_mcp/adapters/modal_adapter.py`, replace `ModalSdkAdapter.__init__` so it accepts a pre-built `RefCodec`:

```python
    def __init__(
        self,
        settings: Settings,
        rpc: ModalRpcClient,
        ref_codec: RefCodec,
    ) -> None:
        self._settings = settings
        self._rpc = rpc
        self._ref_codec = ref_codec
        keys = self._signing_keys
        self._workspace_normalizer = WorkspaceNormalizer(signing_keys=keys)
        self._environment_normalizer = EnvironmentNormalizer(signing_keys=keys)
        self._app_normalizer = AppNormalizer(signing_keys=keys)
        self._container_normalizer = ContainerNormalizer(signing_keys=keys)
        self._volume_normalizer = VolumeNormalizer(signing_keys=keys)
        self._sandbox_normalizer = SandboxNormalizer(signing_keys=keys)
        self._deployment_normalizer = DeploymentNormalizer(signing_keys=keys)
        self._log_normalizer = LogBatchNormalizer(signing_keys=keys)
```

- [ ] **6.2** Replace `ModalSdkAdapter.create` (lines ~229-244):

```python
    @classmethod
    async def create(
        cls,
        settings: Settings,
        *,
        client: Any,
        ref_codec: RefCodec,
    ) -> ModalSdkAdapter:
        """Pure assembly: wrap client in ModalRpcClient, store codec, build normalizers.

        Credential resolution and client construction are *not* this class's
        responsibility — they happen in ``CredentialSource.resolve`` and
        ``ModalRpcClient.from_credentials``, orchestrated from
        ``server._default_adapter_factory`` (or a test bootstrapper).
        """
        rpc = client if isinstance(client, ModalRpcClient) else ModalRpcClient(client)
        return cls(settings, rpc, ref_codec)
```

  Note: `client_factory` parameter is dropped. The `isinstance(client, ModalRpcClient)` branch lets the production bootstrap pass an already-constructed `ModalRpcClient` directly while tests can still pass a `FakeClient`.

- [ ] **6.3** Delete the module-level `_build_ref_codec` helper (lines ~57-62). It is no longer needed — callers build the `RefCodec` upstream.

- [ ] **6.4** Run all adapter tests:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_modal_adapter.py -v 2>&1 | tail -40
```

Expected: the two new tests from Step 5 PASS. Several pre-existing tests FAIL because they call `create()` without `ref_codec` — those are fixed in Step 7.

---

## Step 7 — Migrate existing `test_modal_adapter.py` callsites

Every `ModalSdkAdapter.create(...)` callsite must pass `ref_codec=` explicitly. Several tests that exercised credential resolution paths (`test_create_uses_modal_from_env_aio`, `test_create_uses_modal_from_credentials_aio`) are moved into `test_credentials.py` / `tests/unit/test_modal_rpc_client_from_credentials_*` since those concerns no longer live in `ModalSdkAdapter`.

- [ ] **7.1** Add a module-level helper at the top of `tests/unit/test_modal_adapter.py` (after the `SIGNING_KEYS`/`SIGNING_KEY_TEXT` constants):

```python
from modal_mcp.domain.refs import RefCodec

REF_CODEC = RefCodec(SIGNING_KEYS)
```

- [ ] **7.2** Update every `ModalSdkAdapter.create(...)` callsite to pass `ref_codec=REF_CODEC`. The full list (line numbers approximate, scan for `await ModalSdkAdapter.create(`):

  - `test_create_uses_injected_client_and_aclose`
  - `test_aclose_prefers_modal_private_close_aio`
  - `test_list_apps_threads_configured_environment`
  - `test_call_with_reconnect_retries_once` — also: remove `client_factory=...` arg; instead build `rpc = ModalRpcClient(clients[0], client_factory=lambda: clients[1])` and pass `client=rpc, ref_codec=REF_CODEC`.
  - `test_call_with_reconnect_raises_retryable_without_factory`
  - `test_verify_ref_env_rejects_cross_environment_refs`
  - `test_get_app_matches_signed_refs_by_decoded_native_id`
  - `test_get_container_logs_does_not_send_blank_app_id`
  - `test_read_volume_text_returns_only_bounded_bytes`
  - `test_list_apps_returns_partial_results_with_warnings`
  - `test_adapter_normalize_calls_do_not_pass_signing_keys`

  Mechanical replacement template:

  ```python
  adapter = await ModalSdkAdapter.create(
      settings(modal_config_path),
      client=FakeClient(FakeStub()),
      ref_codec=REF_CODEC,
  )
  ```

- [ ] **7.3** Delete `test_create_uses_modal_from_env_aio` and `test_create_uses_modal_from_credentials_aio` — both exercise credential resolution paths that have moved out of `ModalSdkAdapter`. (The replacement coverage lives in `tests/unit/test_credentials.py` and the `from_credentials` tests added in Step 3.)

- [ ] **7.4** Update the reconnect test that previously relied on `create(client_factory=...)`:

  Replace `test_call_with_reconnect_retries_once` body with:

  ```python
  first_stub = FakeStub()
  first_stub.fail_once = True
  second_stub = FakeStub()
  clients = [FakeClient(first_stub), FakeClient(second_stub)]

  from modal_mcp.adapters.modal_adapter import ModalRpcClient
  rpc = ModalRpcClient(clients[0], client_factory=lambda: clients[1])
  adapter = await ModalSdkAdapter.create(
      settings(modal_config_path),
      client=rpc,
      ref_codec=REF_CODEC,
  )

  workspace = adapter.whoami()
  assert workspace.name == "acme"
  assert len(first_stub.requests) == 1
  assert len(second_stub.requests) == 1
  ```

- [ ] **7.5** Run the adapter tests:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_modal_adapter.py -v 2>&1 | tail -40
```

Expected: all tests PASS.

---

## Step 8 — Wire bootstrap orchestration into `server.py`

`_default_adapter_factory` becomes the orchestration seam. Failures surface in `create_mcp`'s caller (i.e., uvicorn / pytest), not inside the lifespan context manager.

- [ ] **8.1** In `src/modal_mcp/server.py`, replace `_default_adapter_factory` (lines ~65-68):

```python
async def _default_adapter_factory(settings: Settings) -> ModalSdkAdapter:
    """Production bootstrap: resolve credentials, build client, assemble adapter.

    Failures at any phase carry provenance:

    - CredentialError mentions which sources were tried (env / TOML at profile).
    - ModalAdapterError(UPSTREAM_ERROR) from the auth ping includes the
      credential source via ``creds.describe()``.
    """
    creds = CredentialSource.resolve(settings)
    rpc = await ModalRpcClient.from_credentials(creds)
    ref_codec = RefCodec(parse_signing_keys(settings.modal_mcp_signing_keys.get_secret_value()))
    return await ModalSdkAdapter.create(
        settings,
        client=rpc,
        ref_codec=ref_codec,
    )
```

- [ ] **8.2** Add imports at the top of `server.py`:

```python
from modal_mcp.adapters.credentials import CredentialSource
from modal_mcp.adapters.modal_adapter import ModalRpcClient
from modal_mcp.domain.refs import RefCodec
```

(Both `parse_signing_keys` and `ModalSdkAdapter` are already imported.)

- [ ] **8.3** Pre-bootstrap in `create_mcp` so failures surface *before* the FastMCP lifespan begins. Insert directly after `resolved_audit_sink = ...` (around line 376):

```python
    # Pre-bootstrap: validate credentials + Modal client *before* FastMCP starts.
    # This moves bootstrap failures out of the lifespan, where they would
    # otherwise be swallowed by FastMCP's startup error reporting, into the
    # synchronous caller path where uvicorn / pytest can surface them.
    if adapter_factory is _default_adapter_factory:
        # Resolve credentials eagerly so a CredentialError surfaces here.
        # The lifespan still calls adapter_factory (which re-resolves), but the
        # eager check catches misconfiguration before any socket is opened.
        from modal_mcp.adapters.credentials import CredentialSource as _CS

        _CS.resolve(resolved_settings)
```

  Rationale: a full pre-build (resolve → client → adapter) would open a Modal connection at module-import time, which breaks tests that never want to talk to Modal. The eager `CredentialSource.resolve` call catches the most common operator misconfiguration (missing tokens, missing profile) without making network calls; the auth probe still runs once the lifespan starts, but its failure no longer crashes inside `FastMCP.run`.

- [ ] **8.4** Verify lifespan teardown still calls `aclose()`. No change needed — the existing `fastmcp_lifespan` already invokes `adapter.aclose()`.

- [ ] **8.5** Run the server tests:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_server_run.py tests/unit/test_adapter_registry.py -v 2>&1 | tail -20
```

Expected: PASS (no behavior change for the happy path).

---

## Step 9 — Update `doctor.py` to report credential provenance

- [ ] **9.1** In `src/modal_mcp/doctor.py`, extend `CredentialProbeResult` (around line 96) with a `profile` field:

```python
@dataclass(frozen=True, slots=True)
class CredentialProbeResult:
    """Outcome of :func:`probe_credentials`."""

    found: bool
    #: One of ``"environ"``, ``"env_file"``, ``"file_backed"``,
    #: ``"modal_toml"``, or ``"none"``.
    source: str
    detail: str
    profile: str | None = None
```

- [ ] **9.2** Update `probe_credentials` (around lines 442-462) to populate `profile` when the source is `modal_toml`. Replace the final `~/.modal.toml` branch:

```python
    # 5. ~/.modal.toml (or override).
    config_path_str = (
        os.environ.get("MODAL_CONFIG_PATH")
        or (
            parsed_env_file_vars.get("MODAL_CONFIG_PATH")
            if parsed_env_file_vars
            else None
        )
        or "~/.modal.toml"
    )
    effective_config_path = (
        modal_config_path
        if modal_config_path is not None
        else Path(config_path_str).expanduser()
    )
    if effective_config_path.is_file():
        profile = (
            os.environ.get("MODAL_PROFILE")
            or (
                parsed_env_file_vars.get("MODAL_PROFILE")
                if parsed_env_file_vars
                else None
            )
            or "default"
        )
        return CredentialProbeResult(
            found=True,
            source="modal_toml",
            detail=str(effective_config_path),
            profile=profile,
        )

    return CredentialProbeResult(found=False, source="none", detail="")
```

- [ ] **9.3** Update the credential reporting block in `run_doctor` (around lines 744-760) to render provenance:

```python
    if cred.found:
        if cred.source == "environ":
            message = "Modal credentials loaded from MODAL_TOKEN_ID env var"
        elif cred.source == "env_file":
            message = f"Modal credentials loaded from .env file: {cred.detail}"
        elif cred.source == "file_backed":
            message = f"Modal credentials loaded from file-backed tokens: {cred.detail}"
        elif cred.source == "modal_toml":
            message = (
                f"Modal credentials loaded from {cred.detail} "
                f"at profile '{cred.profile or 'default'}'"
            )
        else:
            message = f"Modal credentials found ({cred.source}): {cred.detail}"
        report.items.append(
            DiagnosticItem("credentials", CheckStatus.OK, message)
        )
    else:
        report.items.append(
            DiagnosticItem(
                "credentials",
                CheckStatus.WARN,
                "Modal credentials not found"
                " — add MODAL_TOKEN_ID/SECRET or configure ~/.modal.toml",
            )
        )
```

- [ ] **9.4** Run doctor tests:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_doctor.py -v 2>&1 | tail -30
```

  If any test fails because it asserts the old message text (e.g. `"Modal credentials found (environ)"`), update those test assertions to match the new provenance-aware messages. The expected mapping:

  | Old assertion fragment | New assertion fragment |
  |---|---|
  | `"Modal credentials found (environ)"` | `"Modal credentials loaded from MODAL_TOKEN_ID env var"` |
  | `"Modal credentials found (env_file)"` | `"Modal credentials loaded from .env file"` |
  | `"Modal credentials found (file_backed)"` | `"Modal credentials loaded from file-backed tokens"` |
  | `"Modal credentials found (modal_toml)"` | `"Modal credentials loaded from"` + `"at profile"` |

- [ ] **9.5** Run doctor tests again until they pass:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_doctor.py -q 2>&1 | tail -10
```

Expected: all doctor tests PASS.

---

## Step 10 — Add server-level test for bootstrap-failure surface

- [ ] **10.1** Append to `tests/unit/test_server_run.py`:

```python
import pytest
from pydantic import SecretStr

from modal_mcp.adapters.credentials import CredentialError
from modal_mcp.config import Settings
from modal_mcp.server import create_mcp


def test_create_mcp_surfaces_credential_error_before_lifespan(tmp_path) -> None:
    """Missing Modal credentials raise CredentialError synchronously."""

    # Build Settings with a placeholder modal.toml (Settings.validate requires it
    # to exist), then delete the file so CredentialSource.resolve fails.
    config_path = tmp_path / "modal.toml"
    config_path.write_text("[default]\n", encoding="utf-8")
    settings = Settings(
        modal_config_path=config_path,
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
    )
    config_path.unlink()

    with pytest.raises(CredentialError):
        create_mcp(settings, _skip_security_check=True)
```

- [ ] **10.2** Run:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_server_run.py -v 2>&1 | tail -15
```

Expected: PASS.

---

## Step 11 — Lint + final test sweep

- [ ] **11.1** Run ruff on every file touched by this plan:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run ruff check \
  src/modal_mcp/adapters/credentials.py \
  src/modal_mcp/adapters/modal_adapter.py \
  src/modal_mcp/server.py \
  src/modal_mcp/doctor.py \
  src/modal_mcp/config.py \
  tests/unit/test_credentials.py \
  tests/unit/test_modal_adapter.py \
  tests/unit/test_doctor.py \
  tests/unit/test_server_run.py
```

Expected: clean exit (no output).

- [ ] **11.2** Run the full test suite:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest 2>&1 | tail -20
```

Expected: all tests PASS (pre-existing count + 11 new credentials tests + 2 new RPC `from_credentials` tests + 2 new adapter assembly tests + 1 new server bootstrap test = +16).

---

## Step 12 — Commit

- [ ] **12.1** Stage and commit:

```bash
cd "$(git rev-parse --show-toplevel)" && git add \
  src/modal_mcp/adapters/credentials.py \
  src/modal_mcp/adapters/modal_adapter.py \
  src/modal_mcp/server.py \
  src/modal_mcp/doctor.py \
  src/modal_mcp/config.py \
  tests/unit/test_credentials.py \
  tests/unit/test_modal_adapter.py \
  tests/unit/test_doctor.py \
  tests/unit/test_server_run.py && \
git commit -m "$(cat <<'EOF'
refactor(adapter): split ModalSdkAdapter.create into credentials + client + assembly

Decomposes ModalSdkAdapter.create() into three phases owned by separate modules:

- CredentialSource.resolve(settings) -> ModalCredentials with explicit
  source provenance ('env' | 'toml' | 'injected'), profile, and config_path.
- ModalRpcClient.from_credentials(creds) instantiates the Modal SDK client
  and runs a WorkspaceNameLookup auth probe.  Failures carry
  creds.describe() in the error message.
- ModalSdkAdapter.create(settings, *, client, ref_codec) is pure assembly:
  wraps the client in ModalRpcClient (if needed), stores the codec, builds
  the eight normalizers, returns.

server._default_adapter_factory orchestrates the three phases.  create_mcp()
eagerly resolves credentials before FastMCP startup so CredentialError
surfaces in the synchronous caller path instead of inside the lifespan.

doctor.py reports credential provenance: "loaded from MODAL_TOKEN_ID env var"
vs "loaded from ~/.modal.toml at profile 'staging'".  CredentialProbeResult
gains a profile field.

Settings gains modal_profile (MODAL_PROFILE env var).

Closes epo-split-modalsdkadapter-create-int-hnhk

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage** (epic `epo-split-modalsdkadapter-create-int-hnhk`):

- `ModalCredentials` frozen dataclass + provenance fields — Step 2.1 (adds `config_path` for doctor message).
- `CredentialSource.resolve(settings) -> ModalCredentials` — Step 2.1.
- Modal client factory absorbed into `ModalRpcClient.from_credentials` — Step 4.1.
- `ModalSdkAdapter.create(settings, *, client, ref_codec)` pure assembly — Steps 6.1, 6.2.
- `server.py` orchestration BEFORE FastMCP lifespan — Steps 8.1, 8.3.
- `doctor.py` reports credential source ("loaded from … env var" / "at profile …") — Steps 9.1-9.3.
- `CredentialError` (resolution) vs reused `ModalAdapterError(UPSTREAM_ERROR)` (auth ping) — Steps 2.1, 4.1.
- Test split per module (`test_credentials.py` new; `test_modal_adapter.py` shrinks) — Steps 1.1, 7.3.

**Decision recap (factory placement):** the epic offered "extract `client.py` OR absorb into `ModalRpcClient`". This plan chooses **absorb** so the full client lifecycle (construct, use, close, reconnect) lives in one class. A separate `ModalClientFactory` would be a single-method class whose only job is to return a `ModalRpcClient` — thinner than its receiver.

**Placeholder scan:** no `...`, `TODO`, or `pass` stubs. All method bodies are complete.

**Type consistency:** `ModalCredentials` uses `SecretStr` for tokens. `CredentialSource.resolve` returns `ModalCredentials`. `ModalRpcClient.from_credentials` returns `ModalRpcClient` (raises `ModalAdapterError(UPSTREAM_ERROR)` on probe failure). `ModalSdkAdapter.create` requires kw-only `client: Any` + `ref_codec: RefCodec`; the `isinstance(client, ModalRpcClient)` check lets prod pass a pre-built RPC while tests pass `FakeClient`. `CredentialProbeResult.profile: str | None = None`. `Settings.modal_profile: str | None = None`.

**Wire format invariants:** `modal.toml` parsing and `modal.Client.from_credentials.aio` are unchanged. One additive env var: `MODAL_PROFILE` (default `default`).

**Removed APIs:** `ModalSdkAdapter._create_modal_client`; module helper `_build_ref_codec`; `client_factory` param on `ModalSdkAdapter.create` (moved to `ModalRpcClient.__init__`).

**Test deletions** (Step 7.3): `test_create_uses_modal_from_env_aio`, `test_create_uses_modal_from_credentials_aio`. Replacement coverage in `test_credentials.py` and `test_modal_rpc_client_from_credentials_*`.

**Backward compatibility:** none required — `ModalSdkAdapter.create` is internal; external entry points (`create_mcp`, `create_asgi_app`, `run`) are unchanged.
