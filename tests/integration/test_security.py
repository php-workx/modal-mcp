"""Integration-level security checks for startup configuration."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import modal_mcp.config as config
from modal_mcp.asgi import OriginGuard, OriginValidationError, validate_origin
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


def hosted_settings(tmp_path: Path) -> Settings:
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

    settings = hosted_settings(tmp_path)

    with pytest.raises(ConfigError, match=r"CONFIG_CONFLICT|hosted mode"):
        assert_runtime_security(settings)


def test_create_asgi_app_refuses_hosted_mode_before_tool_serving(
    tmp_path: Path,
) -> None:
    """The ASGI entrypoint must not serve hosted mode through the global adapter."""

    settings = hosted_settings(tmp_path)

    async def adapter_factory(_: Settings) -> object:
        return object()

    with pytest.raises(ConfigError, match=r"CONFIG_CONFLICT|hosted mode"):
        create_asgi_app(settings, adapter_factory=adapter_factory)


def _expert_settings(tmp_path: Path) -> Settings:
    """Return settings that request expert toolset enablement."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\\n", encoding="utf-8")
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


@pytest.mark.parametrize(
    ("origin", "message"),
    [
        (None, "missing Origin header"),
        ("null", "null Origin header"),
        ("chrome-extension://abcd", "unsupported Origin value"),
        ("ftp://mcp.example.com", "unsupported Origin value"),
    ],
)
def test_validate_origin_rejects_invalid_or_missing_origin(
    security_settings: Settings,
    origin: str | None,
    message: str,
) -> None:
    """Missing, null, and non-HTTP origins fail before request handling."""

    with pytest.raises(OriginValidationError, match=message):
        validate_origin(origin, "localhost:8765", security_settings)


def test_validate_origin_rejects_unlisted_origin_and_host(
    security_settings: Settings,
) -> None:
    """Host and origin allowlists both gate request admission."""

    with pytest.raises(OriginValidationError, match="origin is not allowlisted"):
        validate_origin("https://evil.example.com", "localhost:8765", security_settings)

    with pytest.raises(OriginValidationError, match="host is not allowlisted"):
        validate_origin(
            "https://mcp.example.com",
            "evil.example.com",
            security_settings,
        )


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        ("http://127.0.0.1:8765", "localhost:8765"),
        ("https://mcp.example.com", "mcp.example.com"),
    ],
)
def test_validate_origin_accepts_allowed_origins(
    security_settings: Settings,
    origin: str,
    host: str,
) -> None:
    """Configured self-hosted and remote origins are both accepted."""

    validate_origin(origin, host, security_settings)


@pytest.mark.asyncio
async def test_origin_guard_rejects_before_downstream_app(
    security_settings: Settings,
) -> None:
    """Rejected requests never reach the wrapped ASGI app."""

    called = False

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive, send
        nonlocal called
        called = True

    guard = OriginGuard(downstream, security_settings)
    messages = await _invoke(
        guard,
        _http_scope("https://evil.example.com", "localhost:8765"),
    )

    assert called is False
    assert messages[0]["status"] == 403
    assert messages[1]["body"] == b"Forbidden"


@pytest.mark.asyncio
async def test_origin_guard_allows_valid_request_to_pass_through(
    security_settings: Settings,
) -> None:
    """Valid local requests pass through to the wrapped ASGI app."""

    called = False

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive
        nonlocal called
        called = True
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    guard = OriginGuard(downstream, security_settings)
    messages = await _invoke(
        guard,
        _http_scope("http://127.0.0.1:8765", "localhost:8765"),
    )

    assert called is True
    assert messages[0]["status"] == 204
