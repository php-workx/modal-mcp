"""Opt-in live Modal integration smoke tests.

These tests intentionally skip unless `MODAL_MCP_LIVE=1` is set. They are
intended for maintainers with a non-production Modal workspace and read-only
service-user credentials.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from modal_mcp.adapters.modal_adapter import ModalSdkAdapter
from modal_mcp.config import Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("MODAL_MCP_LIVE") != "1",
    reason="set MODAL_MCP_LIVE=1 to run live Modal integration tests",
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required for live Modal integration tests")
    return value


@pytest.mark.asyncio
async def test_live_modal_credentials_can_list_read_only_surfaces() -> None:
    """Smoke check a non-production Modal account with read-only operations."""

    token_id = _require_env("MODAL_TOKEN_ID")
    token_secret = _require_env("MODAL_TOKEN_SECRET")
    signing_keys = _require_env("MODAL_MCP_SIGNING_KEYS")
    environment = _require_env("MODAL_ENVIRONMENT")

    settings = Settings(
        modal_token_id=SecretStr(token_id),
        modal_token_secret=SecretStr(token_secret),
        modal_environment=environment,
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_signing_keys=SecretStr(signing_keys),
    )
    adapter = await ModalSdkAdapter.create(settings)
    try:
        adapter.validate_auth()
        assert adapter.list_environments()
        adapter.list_apps(environment)
    finally:
        close = getattr(adapter, "aclose", None)
        if close is not None:
            await close()
