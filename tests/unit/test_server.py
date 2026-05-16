"""Unit tests for server-level wiring invariants."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from modal_mcp.config import ConfigError, Settings
from modal_mcp.policy.rules import CHANGE_TOOLSETS, READ_ONLY_TOOLSETS
from modal_mcp.server import create_mcp


def test_create_mcp_raises_config_error_when_signing_keys_missing(
    tmp_path: Path,
) -> None:
    """create_mcp surfaces PolicyContext.from_settings invariant failures."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    settings = Settings(
        modal_config_path=modal_config,
        modal_token_id=SecretStr("ak-id"),
        modal_token_secret=SecretStr("ak-secret"),
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_allowed_hosts=("127.0.0.1", "localhost"),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
        modal_mcp_read_only=False,
        modal_mcp_enabled_toolsets=READ_ONLY_TOOLSETS | CHANGE_TOOLSETS,
    )
    object.__setattr__(settings, "modal_mcp_signing_keys", None)

    with pytest.raises(ConfigError, match="signing keys"):
        create_mcp(settings, _skip_security_check=True)
