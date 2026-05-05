"""Opt-in live Modal integration smoke tests.

These tests intentionally skip unless ``MODAL_MCP_LIVE=1`` is set.  They are
intended for maintainers with a non-production Modal workspace and read-only
service-user credentials.

Setup
-----
The recommended path uses the local setup flow:

1.  Run ``modal-mcp setup --yes`` to generate ``.env`` and
    ``.secrets/signing-key.txt``.
2.  Create credential files containing your Modal token values, e.g.::

        echo "ak-..." > ~/.secrets/modal-token-id.txt
        echo "as-..." > ~/.secrets/modal-token-secret.txt
        chmod 600 ~/.secrets/modal-token-id.txt ~/.secrets/modal-token-secret.txt

3.  Add the file references and your target environment to ``.env``::

        MODAL_TOKEN_ID_FILE=/path/to/modal-token-id.txt
        MODAL_TOKEN_SECRET_FILE=/path/to/modal-token-secret.txt
        MODAL_ENVIRONMENT=main

4.  Run the live suite::

        MODAL_MCP_LIVE=1 just test-live

Alternatively, supply credentials directly as environment variables without a
``.env`` file::

    MODAL_TOKEN_ID=<id> MODAL_TOKEN_SECRET=<secret> \\
    MODAL_MCP_SIGNING_KEYS=<key> MODAL_ENVIRONMENT=<env> \\
    MODAL_MCP_LIVE=1 just test-live

pydantic-settings resolves credentials in this priority order:
constructor kwargs → environment variables → ``.env`` file → defaults.
File-backed secrets (``MODAL_TOKEN_ID_FILE``, ``MODAL_TOKEN_SECRET_FILE``,
``MODAL_MCP_SIGNING_KEY_FILE``) are expanded automatically by
:class:`~modal_mcp.config.Settings` before the startup contract is validated.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from modal_mcp.adapters.modal_adapter import ModalSdkAdapter
from modal_mcp.config import ConfigError, Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("MODAL_MCP_LIVE") != "1",
    reason="set MODAL_MCP_LIVE=1 to run live Modal integration tests",
)

# Placeholder used for MODAL_MCP_ALLOWED_ORIGINS when constructing Settings for
# the smoke test.  Origin enforcement is a server-layer concern; the adapter
# smoke check only validates Modal credential validity and read-only surfaces.
_SMOKE_ORIGIN: str = "http://127.0.0.1:8765"


def _load_settings() -> Settings:
    """Load Settings from ``.env`` and/or environment variables.

    Credential resolution follows the pydantic-settings priority chain:

    - Constructor kwargs (highest priority; used here only for ``allowed_origins``)
    - Environment variables
    - ``.env`` file (auto-loaded from the current working directory)
    - Defaults

    File-backed variants resolved automatically by Settings before the startup
    contract runs:

    - ``MODAL_TOKEN_ID_FILE``      → ``modal_token_id``
    - ``MODAL_TOKEN_SECRET_FILE``  → ``modal_token_secret``
    - ``MODAL_MCP_SIGNING_KEY_FILE`` → ``modal_mcp_signing_keys``

    If settings cannot be loaded (e.g. missing credentials) the test is skipped
    with an actionable message rather than failing.
    """
    try:
        settings = Settings(
            # Provide a smoke-test placeholder for MODAL_MCP_ALLOWED_ORIGINS.
            # The field is required by the startup contract but is irrelevant to
            # the adapter-level smoke check.
            modal_mcp_allowed_origins=(_SMOKE_ORIGIN,),
        )
    except (ConfigError, ValidationError) as exc:
        msg = str(exc)
        expected = (
            "Modal credentials are required",
            "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be provided together",
            "MODAL_MCP_SIGNING_KEYS",
        )
        if not any(token in msg for token in expected):
            raise
        pytest.skip(
            f"Live smoke test settings could not be loaded: {exc}\n"
            "\n"
            "Credential options (set in .env or as environment variables):\n"
            "  File-backed:  MODAL_TOKEN_ID_FILE=<path>  "
            "MODAL_TOKEN_SECRET_FILE=<path>\n"
            "  Direct:       MODAL_TOKEN_ID=<id>  MODAL_TOKEN_SECRET=<secret>\n"
            "  toml:         MODAL_CONFIG_PATH or ~/.modal.toml\n"
            "\n"
            "Also required: MODAL_ENVIRONMENT and "
            "MODAL_MCP_SIGNING_KEYS (or MODAL_MCP_SIGNING_KEY_FILE).\n"
            "\n"
            "See the module docstring in this file for the full setup flow."
        )

    if settings.modal_environment is None:
        pytest.skip(
            "MODAL_ENVIRONMENT is required for live tests. "
            "Set it in the environment or add it to .env."
        )

    # Settings._has_modal_credentials() accepts credentials from
    # MODAL_TOKEN_*_FILE or ~/.modal.toml, but the adapter needs explicit token
    # values at this layer (it cannot read ~/.modal.toml directly).
    if settings.modal_token_id is None or settings.modal_token_secret is None:
        pytest.skip(
            "Explicit Modal token credentials not found.  "
            "Set MODAL_TOKEN_ID + MODAL_TOKEN_SECRET or "
            "MODAL_TOKEN_ID_FILE + MODAL_TOKEN_SECRET_FILE in .env or env."
        )

    return settings


@pytest.mark.asyncio
async def test_live_modal_credentials_can_list_read_only_surfaces() -> None:
    """Smoke check a non-production Modal account with read-only operations."""

    settings = _load_settings()
    environment = settings.modal_environment
    assert environment is not None  # guaranteed by _load_settings

    adapter = await ModalSdkAdapter.create(settings)
    try:
        adapter.validate_auth()
        assert adapter.list_environments()
        adapter.list_apps(environment)
    finally:
        close = getattr(adapter, "aclose", None)
        if close is not None:
            await close()
