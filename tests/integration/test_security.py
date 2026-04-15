"""Integration-level security checks for startup configuration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from modal_mcp.config import Settings, assert_runtime_security


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
        "MODAL_MCP_SIGNING_KEYS",
        "MODAL_MCP_AUTH_MODE",
        "MODAL_MCP_DEBUG",
        "MODAL_MCP_DEBUG_EXPOSE_IDS",
        "MODAL_MCP_CLI_FALLBACK",
    ):
        monkeypatch.delenv(key, raising=False)


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
