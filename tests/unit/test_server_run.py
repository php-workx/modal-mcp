"""Unit tests for server.run() without a live socket or Modal credentials."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from modal_mcp.server import run


def test_run_uses_configured_bind_and_asgi_app() -> None:
    """run(settings) must pass host, port, and ASGI app from settings to uvicorn."""
    fake_app = MagicMock()
    fake_settings = MagicMock()
    fake_settings.modal_mcp_http_bind = "127.0.0.1:9876"

    with (
        patch("modal_mcp.server.create_asgi_app", return_value=fake_app) as mock_create,
        patch("modal_mcp.server.uvicorn.run") as mock_uvicorn,
    ):
        run(fake_settings)

    mock_create.assert_called_once_with(fake_settings)
    mock_uvicorn.assert_called_once_with(fake_app, host="127.0.0.1", port=9876)
