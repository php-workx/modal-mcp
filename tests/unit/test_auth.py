"""Unit tests for authentication helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from fastmcp.server.auth import MultiAuth
from fastmcp.server.auth.providers.jwt import JWTVerifier

from modal_mcp.auth import STATIC_BEARER_SCOPE, StaticTokenVerifier, build_auth
from modal_mcp.config import Settings


def base_settings_kwargs(modal_config_path: Path) -> dict[str, object]:
    """Return the minimum settings required for auth tests."""

    return {
        "modal_config_path": modal_config_path,
        "modal_mcp_allowed_origins": ("http://127.0.0.1:8765",),
        "modal_mcp_signing_keys": SecretStr("kid1:" + "a" * 64),
    }


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


@pytest.mark.parametrize("auth_mode", ["hosted_jwt", "hosted_oauth"])
def test_build_auth_returns_jwt_verifier_for_hosted_modes(
    auth_mode: str,
    modal_config_path: Path,
) -> None:
    """Hosted auth modes build a JWT verifier from validated settings."""

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

    assert isinstance(auth, JWTVerifier)
    assert auth.issuer == "https://issuer.example.com"
    assert auth.jwks_uri == "https://issuer.example.com/jwks.json"
    assert auth.audience == "modal-mcp"


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
    assert auth.server is None
    assert len(auth.verifiers) == 2
    assert isinstance(auth.verifiers[0], StaticTokenVerifier)
    assert isinstance(auth.verifiers[1], JWTVerifier)
