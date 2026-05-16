"""Integration-level security checks for startup configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

import modal_mcp.config as config
from modal_mcp.asgi import OriginGuard
from modal_mcp.config import ConfigError, Settings, assert_runtime_security
from modal_mcp.server import create_asgi_app


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep security tests independent from the operator environment."""

    for key in (
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "MODAL_TOKEN_ID_FILE",
        "MODAL_TOKEN_SECRET_FILE",
        "MODAL_CONFIG_PATH",
        "MODAL_MCP_ALLOWED_ORIGINS",
        "MODAL_MCP_ALLOWED_HOSTS",
        "MODAL_MCP_HTTP_BIND",
        "MODAL_MCP_PUBLIC_ORIGIN",
        "MODAL_MCP_SIGNING_KEYS",
        "MODAL_MCP_AUTH_MODE",
        "MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE",
        "MODAL_MCP_AUTH_ISSUER",
        "MODAL_MCP_AUTH_JWKS_URI",
        "MODAL_MCP_AUTH_AUDIENCE",
        "MODAL_MCP_ALLOWED_REDIRECT_URIS",
        "MODAL_MCP_DEBUG",
        "MODAL_MCP_DEBUG_EXPOSE_IDS",
        "MODAL_MCP_CLI_FALLBACK",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def security_settings(tmp_path: Path) -> Settings:
    """Return settings that allow one local and one remote origin."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    return Settings(
        modal_config_path=modal_config,
        modal_mcp_allowed_origins=(
            "http://127.0.0.1:8765",
            "https://mcp.example.com",
        ),
        modal_mcp_allowed_hosts=("127.0.0.1", "localhost", "mcp.example.com"),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
    )


def make_hosted_settings(tmp_path: Path) -> Settings:
    """Return hosted settings that should currently fail at startup."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    return Settings(
        modal_config_path=modal_config,
        modal_mcp_allowed_origins=("https://mcp.example.com",),
        modal_mcp_allowed_hosts=("mcp.example.com",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
        modal_mcp_auth_mode="hosted_jwt",
        modal_mcp_public_origin="https://mcp.example.com",
        modal_mcp_auth_issuer="https://issuer.example.com",
        modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
        modal_mcp_auth_audience="modal-mcp",
        modal_mcp_allowed_redirect_uris=("https://client.example.com/cb",),
    )


def _http_scope(origin: str | None, host: str | None) -> dict[str, Any]:
    headers: list[tuple[bytes, bytes]] = []
    if host is not None:
        headers.append((b"host", host.encode("latin-1")))
    if origin is not None:
        headers.append((b"origin", origin.encode("latin-1")))
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": headers,
    }


async def _invoke(app: OriginGuard, scope: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return messages


def test_runtime_security_allows_self_hosted_defaults(tmp_path: Path) -> None:
    """Best-effort process hardening does not fail on supported defaults."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    settings = Settings(
        modal_config_path=modal_config,
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
    )

    assert_runtime_security(settings)


def test_runtime_security_refuses_hosted_mode_until_session_resolution_exists(
    tmp_path: Path,
) -> None:
    """Hosted mode must fail closed before request-scoped session support lands."""

    settings = make_hosted_settings(tmp_path)

    with pytest.raises(ConfigError, match=r"CONFIG_CONFLICT|hosted mode"):
        assert_runtime_security(settings)


def test_create_asgi_app_refuses_hosted_mode_before_tool_serving(
    tmp_path: Path,
) -> None:
    """The ASGI entrypoint must not serve hosted mode through the global adapter."""

    settings = make_hosted_settings(tmp_path)

    async def adapter_factory(_: Settings) -> object:
        return object()

    with pytest.raises(ConfigError, match=r"CONFIG_CONFLICT|hosted mode"):
        create_asgi_app(settings, adapter_factory=adapter_factory)


def _expert_settings(tmp_path: Path) -> Settings:
    """Return settings that request expert toolset enablement."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    return Settings(
        modal_config_path=modal_config,
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
        modal_mcp_enabled_toolsets=(
            "discovery",
            "apps",
            "containers",
            "logs",
            "volumes",
            "sandboxes",
            "expert",
        ),
    )


def test_expert_mode_refuses_non_linux_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expert mode must fail closed before startup on unsupported OSes."""

    settings = _expert_settings(tmp_path)
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")

    with pytest.raises(ConfigError, match="CONFIG_CONFLICT"):
        assert_runtime_security(settings)


def test_expert_mode_refuses_missing_namespace_or_cgroup_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expert mode requires namespace and cgroup controls for safe sandboxing."""

    settings = _expert_settings(tmp_path)
    monkeypatch.setattr(config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(config, "_supports_expert_process_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_filesystem_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_network_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_namespace_controls", lambda: False)
    monkeypatch.setattr(config, "_supports_expert_cgroup_controls", lambda: False)
    monkeypatch.setattr(
        config,
        "_supports_expert_proc_masking",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(config, "_supports_expert_rlimit_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_rpc_bridge", lambda: True)

    with pytest.raises(ConfigError, match=r"namespace controls|cgroup controls"):
        assert_runtime_security(settings)


def test_expert_mode_refuses_missing_proc_masking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If proc masking primitives are unavailable, expert startup must fail."""

    settings = _expert_settings(tmp_path)
    monkeypatch.setattr(config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(config, "_supports_expert_process_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_filesystem_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_network_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_namespace_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_cgroup_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_rlimit_controls", lambda: True)
    monkeypatch.setattr(
        config,
        "_supports_expert_proc_masking",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(config, "_supports_expert_rpc_bridge", lambda: True)

    with pytest.raises(ConfigError, match="proc masking"):
        assert_runtime_security(settings)


def test_expert_mode_allows_supported_linux_capability_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulated Linux capability fixture should allow expert startup gating."""

    settings = _expert_settings(tmp_path)
    monkeypatch.setattr(config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(config, "_supports_expert_process_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_filesystem_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_network_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_namespace_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_cgroup_controls", lambda: True)
    monkeypatch.setattr(
        config,
        "_supports_expert_proc_masking",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(config, "_supports_expert_rlimit_controls", lambda: True)
    monkeypatch.setattr(config, "_supports_expert_rpc_bridge", lambda: True)

    assert_runtime_security(settings)


def test_hosted_debug_flags_fail_before_runtime() -> None:
    """Unsafe hosted debug flags fail fast during settings validation."""

    with pytest.raises(ValidationError, match="MODAL_MCP_DEBUG_EXPOSE_IDS"):
        Settings(
            modal_token_id=SecretStr("tid"),
            modal_token_secret=SecretStr("tsecret"),
            modal_mcp_allowed_origins=("https://mcp.example.com",),
            modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
            modal_mcp_auth_mode="hosted_jwt",
            modal_mcp_public_origin="https://mcp.example.com",
            modal_mcp_auth_issuer="https://issuer.example.com",
            modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
            modal_mcp_auth_audience="modal-mcp",
            modal_mcp_allowed_redirect_uris=("https://client.example.com/cb",),
            modal_mcp_debug_expose_ids=True,
        )


def test_hosted_cli_fallback_flag_is_rejected_before_runtime() -> None:
    """Hosted auth modes refuse the dead CLI fallback flag too."""

    with pytest.raises(ValidationError, match="MODAL_MCP_CLI_FALLBACK"):
        Settings(
            modal_token_id=SecretStr("tid"),
            modal_token_secret=SecretStr("tsecret"),
            modal_mcp_allowed_origins=("https://mcp.example.com",),
            modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
            modal_mcp_auth_mode="hosted_oauth",
            modal_mcp_public_origin="https://mcp.example.com",
            modal_mcp_auth_issuer="https://issuer.example.com",
            modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
            modal_mcp_auth_audience="modal-mcp",
            modal_mcp_allowed_redirect_uris=("https://client.example.com/cb",),
            modal_mcp_cli_fallback=True,
        )


# --- OriginGuard construction-time validation -------------------------------


@pytest.mark.parametrize(
    ("bad_entry", "kind"),
    [
        ("ftp://mcp.example.com", "origin"),
        ("http://user:pw@mcp.example.com", "origin"),
        ("http://mcp.example.com/path", "origin"),
        ("http://mcp.example.com?x=1", "origin"),
        ("http://mcp.example.com#frag", "origin"),
        ("null", "origin"),
        ("", "origin"),
    ],
)
def test_origin_guard_init_rejects_malformed_origin_entry(
    bad_entry: str,
    kind: str,
) -> None:
    """Malformed allowed-origin entries fail loudly at startup."""

    del kind  # used only for parameter labelling
    with pytest.raises(ConfigError, match=r"MODAL_MCP_ALLOWED_ORIGINS"):
        OriginGuard(
            _noop_app,
            allowed_origins=("http://127.0.0.1:8765", bad_entry),
            allowed_hosts=("127.0.0.1",),
        )


@pytest.mark.parametrize(
    "bad_entry",
    [
        "http://user@host",
        "host:not-a-port",
        "host/path",
        "host?x=1",
        "",
    ],
)
def test_origin_guard_init_rejects_malformed_host_entry(bad_entry: str) -> None:
    """Malformed allowed-host entries fail loudly at startup."""

    with pytest.raises(ConfigError, match=r"MODAL_MCP_ALLOWED_HOSTS"):
        OriginGuard(
            _noop_app,
            allowed_origins=("http://127.0.0.1:8765",),
            allowed_hosts=("127.0.0.1", bad_entry),
        )


def test_origin_guard_init_names_offending_value_in_error() -> None:
    """The ConfigError message includes the bad value (so operators can grep logs)."""

    with pytest.raises(ConfigError, match=r"ftp://bad\.example\.com"):
        OriginGuard(
            _noop_app,
            allowed_origins=("http://127.0.0.1:8765", "ftp://bad.example.com"),
            allowed_hosts=("127.0.0.1",),
        )


def test_origin_guard_init_does_not_store_settings() -> None:
    """The guard must not retain Settings; only precomputed sets are kept."""

    guard = OriginGuard(
        _noop_app,
        allowed_origins=("http://127.0.0.1:8765",),
        allowed_hosts=("127.0.0.1", "localhost"),
    )

    # OriginGuard uses __slots__ so we walk those instead of __dict__.
    for attr in OriginGuard.__slots__:
        value = getattr(guard, attr)
        assert not isinstance(value, Settings), (
            f"OriginGuard.{attr} unexpectedly holds Settings"
        )


def test_origin_guard_init_precomputes_frozen_sets() -> None:
    """Allowed sets are immutable frozensets, normalised once at construction."""

    guard = OriginGuard(
        _noop_app,
        allowed_origins=("HTTP://127.0.0.1:8765",),
        allowed_hosts=("LocalHost",),
    )

    # Private attributes are part of this module's contract; tests intentionally
    # peek to verify the precomputation invariant.
    allowed_origins = guard._allowed_origins
    allowed_hosts = guard._allowed_hosts
    assert isinstance(allowed_origins, frozenset)
    assert isinstance(allowed_hosts, frozenset)
    assert allowed_origins == frozenset({"http://127.0.0.1:8765"})
    assert allowed_hosts == frozenset({"localhost"})


def test_origin_guard_init_accepts_empty_inputs_as_caller_responsibility() -> None:
    """Empty inputs build an empty allowlist; Settings layer enforces non-empty."""

    guard = OriginGuard(
        _noop_app,
        allowed_origins=(),
        allowed_hosts=(),
    )
    assert guard._allowed_origins == frozenset()
    assert guard._allowed_hosts == frozenset()


# --- OriginGuard runtime hot path -------------------------------------------


def _make_guard(
    *,
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8765",
        "https://mcp.example.com",
    ),
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "mcp.example.com"),
    downstream: Any | None = None,
) -> OriginGuard:
    async def _noop(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive, send

    return OriginGuard(
        downstream or _noop,
        allowed_origins=allowed_origins,
        allowed_hosts=allowed_hosts,
    )


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        ("http://127.0.0.1:8765", "localhost:8765"),
        ("https://mcp.example.com", "mcp.example.com"),
    ],
)
@pytest.mark.asyncio
async def test_origin_guard_accepts_allowlisted_requests(
    origin: str,
    host: str,
) -> None:
    """Allowlisted (origin, host) pairs pass through to the wrapped app."""

    called = False

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guard = _make_guard(downstream=downstream)
    messages = await _invoke(guard, _http_scope(origin, host))

    assert called is True
    assert messages[0]["status"] == 204


@pytest.mark.parametrize(
    "origin",
    [None, "null", "chrome-extension://abcd", "ftp://mcp.example.com"],
)
@pytest.mark.asyncio
async def test_origin_guard_rejects_invalid_or_missing_origin(
    origin: str | None,
) -> None:
    """Missing/null/non-HTTP request origins fail closed with 403."""

    guard = _make_guard()
    messages = await _invoke(guard, _http_scope(origin, "localhost:8765"))

    assert messages[0]["status"] == 403


@pytest.mark.asyncio
async def test_origin_guard_rejects_unlisted_origin() -> None:
    """Origins outside the precomputed set fail closed with 403."""

    guard = _make_guard()
    messages = await _invoke(
        guard,
        _http_scope("https://evil.example.com", "localhost:8765"),
    )

    assert messages[0]["status"] == 403


@pytest.mark.asyncio
async def test_origin_guard_rejects_unlisted_host() -> None:
    """Hosts outside the precomputed set fail closed with 403."""

    guard = _make_guard()
    messages = await _invoke(
        guard,
        _http_scope("https://mcp.example.com", "evil.example.com"),
    )

    assert messages[0]["status"] == 403


@pytest.mark.asyncio
async def test_origin_guard_rejects_missing_host_header() -> None:
    """Missing Host header fails closed instead of trusting the ASGI server bind."""

    called = False

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive, send
        nonlocal called
        called = True

    guard = _make_guard(downstream=downstream)
    scope = _http_scope("http://127.0.0.1:8765", None)
    scope["server"] = ("127.0.0.1", 8765)
    messages = await _invoke(guard, scope)

    assert called is False
    assert messages[0]["status"] == 403


async def _noop_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    del scope, receive, send
