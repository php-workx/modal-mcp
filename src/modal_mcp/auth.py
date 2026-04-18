"""Authentication helpers for Modal MCP."""

from __future__ import annotations

from typing import Any, cast

from fastmcp.server.auth import (
    AccessToken,
    MultiAuth,
    RemoteAuthProvider,
    TokenVerifier,
)
from fastmcp.server.auth import StaticTokenVerifier as FastMCPStaticTokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl, SecretStr

from modal_mcp.config import HOSTED_AUTH_MODES, Settings, load_secret_file

STATIC_BEARER_CLIENT_ID = "self-hosted"
STATIC_BEARER_SCOPE = "modal-mcp:all"


class StaticTokenVerifier(TokenVerifier):
    """Verify a fixed bearer token or a fixed token map."""

    def __init__(
        self,
        tokens: dict[str, dict[str, Any]] | SecretStr | str,
        required_scopes: list[str] | None = None,
    ) -> None:
        super().__init__(required_scopes=required_scopes)
        if isinstance(tokens, dict):
            token_map = tokens
        else:
            token_value = (
                tokens.get_secret_value() if isinstance(tokens, SecretStr) else tokens
            )
            token_map = {
                token_value: {
                    "client_id": STATIC_BEARER_CLIENT_ID,
                    "scopes": required_scopes or [STATIC_BEARER_SCOPE],
                }
            }
        self._delegate = FastMCPStaticTokenVerifier(
            tokens=token_map,
            required_scopes=required_scopes,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return access metadata for the configured bearer token."""

        return await self._delegate.verify_token(token)


def _load_static_bearer_verifier(settings: Settings) -> StaticTokenVerifier | None:
    """Build the local bearer verifier when a token file is configured."""

    token_file = settings.modal_mcp_self_hosted_bearer_token_file
    if token_file is None:
        return None
    bearer_token = load_secret_file(token_file)
    return StaticTokenVerifier(bearer_token, required_scopes=[STATIC_BEARER_SCOPE])


def _load_jwt_verifier(settings: Settings) -> JWTVerifier | None:
    """Build the hosted JWT verifier for hosted credential modes."""

    if settings.modal_mcp_auth_mode not in HOSTED_AUTH_MODES:
        return None
    return JWTVerifier(
        jwks_uri=settings.modal_mcp_auth_jwks_uri,
        issuer=settings.modal_mcp_auth_issuer,
        audience=settings.modal_mcp_auth_audience,
        base_url=settings.modal_mcp_public_origin,
    )


def _build_hosted_auth_provider(settings: Settings) -> RemoteAuthProvider:
    verifier = _load_jwt_verifier(settings)
    if verifier is None:
        msg = "hosted auth mode requires JWT verification settings"
        raise ValueError(msg)
    issuer = settings.modal_mcp_auth_issuer
    public_origin = settings.modal_mcp_public_origin
    if issuer is None or public_origin is None:
        msg = "hosted auth mode requires a public origin and issuer"
        raise ValueError(msg)
    # FastMCP 3.2.x does not accept a redirect-allowlist parameter here.
    # The allowlist is enforced during settings validation instead.
    issuer_url = cast(AnyHttpUrl, issuer)
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[issuer_url],
        base_url=public_origin,
    )


def build_auth(
    settings: Settings,
) -> TokenVerifier | MultiAuth | RemoteAuthProvider | None:
    """Build the FastMCP auth provider graph for the current settings."""

    if settings.modal_mcp_auth_mode not in HOSTED_AUTH_MODES:
        return _load_static_bearer_verifier(settings)

    hosted_server = _build_hosted_auth_provider(settings)

    static_verifier = _load_static_bearer_verifier(settings)
    if static_verifier is None:
        return hosted_server

    return MultiAuth(server=hosted_server, verifiers=[static_verifier])


__all__ = [
    "STATIC_BEARER_CLIENT_ID",
    "STATIC_BEARER_SCOPE",
    "StaticTokenVerifier",
    "build_auth",
]
