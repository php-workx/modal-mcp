"""Authentication helpers for Modal MCP."""

from __future__ import annotations

from typing import Any

from fastmcp.server.auth import (
    AccessToken,
    MultiAuth,
    TokenVerifier,
)
from fastmcp.server.auth import (
    StaticTokenVerifier as FastMCPStaticTokenVerifier,
)
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import SecretStr

from modal_mcp.config import Settings, load_secret_file

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

    if settings.modal_mcp_auth_mode not in {"hosted_jwt", "hosted_oauth"}:
        return None
    return JWTVerifier(
        jwks_uri=settings.modal_mcp_auth_jwks_uri,
        issuer=settings.modal_mcp_auth_issuer,
        audience=settings.modal_mcp_auth_audience,
        base_url=settings.modal_mcp_public_origin,
    )


def build_auth(settings: Settings) -> TokenVerifier | MultiAuth | None:
    """Build the FastMCP auth provider graph for the current settings."""

    verifiers: list[TokenVerifier] = []

    static_verifier = _load_static_bearer_verifier(settings)
    if static_verifier is not None:
        verifiers.append(static_verifier)

    jwt_verifier = _load_jwt_verifier(settings)
    if jwt_verifier is not None:
        verifiers.append(jwt_verifier)

    if not verifiers:
        return None
    if len(verifiers) == 1:
        return verifiers[0]
    return MultiAuth(verifiers=verifiers)


__all__ = [
    "STATIC_BEARER_CLIENT_ID",
    "STATIC_BEARER_SCOPE",
    "StaticTokenVerifier",
    "build_auth",
]
