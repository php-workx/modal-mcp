"""Unit tests for authentication helpers."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastmcp.server.auth import MultiAuth, RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import SecretStr

from modal_mcp.auth import STATIC_BEARER_SCOPE, StaticTokenVerifier, build_auth
from modal_mcp.config import Settings


def base_settings_kwargs(modal_config_path: Path) -> dict[str, object]:
    """Return the minimum settings required for auth tests."""

    return {
        "modal_config_path": modal_config_path,
        "modal_mcp_allowed_origins": ("http://127.0.0.1:8765",),
        "modal_mcp_signing_keys": SecretStr("kid1:" + "a" * 64),
    }


CONFIG_ENV_KEYS = {
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "MODAL_TOKEN_ID_FILE",
    "MODAL_TOKEN_SECRET_FILE",
    "MODAL_CONFIG_PATH",
    "MODAL_MCP_ALLOWED_ORIGINS",
    "MODAL_MCP_SIGNING_KEYS",
    "MODAL_MCP_SIGNING_KEY_FILE",
    "MODAL_MCP_AUTH_MODE",
    "MODAL_MCP_PUBLIC_ORIGIN",
    "MODAL_MCP_AUTH_ISSUER",
    "MODAL_MCP_AUTH_JWKS_URI",
    "MODAL_MCP_AUTH_AUDIENCE",
    "MODAL_MCP_ALLOWED_REDIRECT_URIS",
    "MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE",
}


@pytest.fixture(autouse=True)
def clean_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep auth tests independent from ambient operator settings."""

    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def modal_config_path(tmp_path: Path) -> Path:
    """Create a placeholder Modal config file for startup validation."""

    path = tmp_path / "modal.toml"
    path.write_text("[default]\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_static_token_verifier_accepts_configured_token() -> None:
    """A configured bearer token is accepted and missing tokens are rejected."""

    verifier = StaticTokenVerifier("bearer-token")

    token = await verifier.verify_token("bearer-token")
    assert token is not None
    assert token.client_id == "self-hosted"
    assert token.scopes == [STATIC_BEARER_SCOPE]
    assert token.claims["client_id"] == "self-hosted"
    assert await verifier.verify_token("invalid-token") is None


def test_build_auth_returns_none_without_self_hosted_bearer_token(
    modal_config_path: Path,
) -> None:
    """Local self-hosted mode stays unauthenticated without a token file."""

    settings = Settings(**base_settings_kwargs(modal_config_path))

    assert build_auth(settings) is None


@pytest.mark.asyncio
async def test_build_auth_returns_static_bearer_verifier(
    modal_config_path: Path,
    tmp_path: Path,
) -> None:
    """Self-hosted bearer auth uses the configured file-backed token."""

    token_file = tmp_path / "bearer-token"
    token_file.write_text("bearer-token\n", encoding="utf-8")
    settings = Settings(
        modal_mcp_self_hosted_bearer_token_file=token_file,
        **base_settings_kwargs(modal_config_path),
    )

    auth = build_auth(settings)

    assert isinstance(auth, StaticTokenVerifier)
    token = await auth.verify_token("bearer-token")
    assert token is not None
    assert token.client_id == "self-hosted"
    assert token.scopes == [STATIC_BEARER_SCOPE]
    assert await auth.verify_token("missing") is None


@pytest.mark.parametrize(
    "auth_mode", ["hosted_jwt", "hosted_oauth", "hosted_read_only_ephemeral"]
)
def test_build_auth_returns_remote_auth_provider_for_hosted_modes(
    auth_mode: str,
    modal_config_path: Path,
) -> None:
    """Hosted auth modes normalize aliases and return a remote provider."""

    settings = Settings(
        modal_mcp_auth_mode=auth_mode,
        modal_mcp_public_origin="https://mcp.example.com",
        modal_mcp_auth_issuer="https://issuer.example.com",
        modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
        modal_mcp_auth_audience="modal-mcp",
        modal_mcp_allowed_redirect_uris=("https://client.example.com/cb",),
        **base_settings_kwargs(modal_config_path),
    )

    auth = build_auth(settings)

    assert isinstance(auth, RemoteAuthProvider)
    assert isinstance(auth.token_verifier, JWTVerifier)
    assert auth.authorization_servers == ["https://issuer.example.com"]
    assert str(auth.base_url).rstrip("/") == "https://mcp.example.com"
    assert settings.modal_mcp_auth_mode == "hosted_read_only_ephemeral"


def test_build_auth_composes_multi_auth_when_two_verifiers_are_configured(
    modal_config_path: Path,
    tmp_path: Path,
) -> None:
    """Hosted auth plus a bearer token file compose with MultiAuth."""

    token_file = tmp_path / "bearer-token"
    token_file.write_text("bearer-token\n", encoding="utf-8")
    settings = Settings(
        modal_mcp_auth_mode="hosted_jwt",
        modal_mcp_public_origin="https://mcp.example.com",
        modal_mcp_auth_issuer="https://issuer.example.com",
        modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
        modal_mcp_auth_audience="modal-mcp",
        modal_mcp_allowed_redirect_uris=("https://client.example.com/cb",),
        modal_mcp_self_hosted_bearer_token_file=token_file,
        **base_settings_kwargs(modal_config_path),
    )

    auth = build_auth(settings)

    assert isinstance(auth, MultiAuth)
    assert isinstance(auth.server, RemoteAuthProvider)
    assert len(auth.verifiers) == 1
    assert isinstance(auth.verifiers[0], StaticTokenVerifier)


def test_remote_auth_provider_has_no_redirect_allowlist_constructor_parameter() -> None:
    """Pinned FastMCP exposes no provider-level redirect allowlist knob."""

    assert (
        "allowed_client_redirect_uris"
        not in inspect.signature(RemoteAuthProvider).parameters
    )
