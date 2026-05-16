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


def test_run_stdio_invokes_fastmcp_with_stdio_transport() -> None:
    """run_stdio(settings) must construct a FastMCP and call mcp.run(transport='stdio').

    Stdio launch is the Codex subprocess path: there is no uvicorn, no
    OriginGuard, no approval HTTP route. We assert the negative (no uvicorn
    call) alongside the positive (FastMCP.run was called with the right
    transport) so that a future refactor cannot silently re-introduce HTTP
    side-effects on the stdio path.
    """
    fake_settings = MagicMock()
    fake_settings.modal_mcp_enabled_toolsets = ["discovery"]
    fake_settings.modal_mcp_read_only = True

    fake_mcp_instance = MagicMock()

    with (
        patch("modal_mcp.server.FastMCP", return_value=fake_mcp_instance) as mock_cls,
        patch("modal_mcp.server.register_toolsets") as mock_register,
        patch("modal_mcp.server.configure_logging"),
        patch("modal_mcp.server.uvicorn.run") as mock_uvicorn,
    ):
        from modal_mcp.server import run_stdio

        run_stdio(fake_settings)

    mock_cls.assert_called_once()
    mock_register.assert_called_once()
    fake_mcp_instance.run.assert_called_once_with(transport="stdio")
    mock_uvicorn.assert_not_called()


def test_run_stdio_disables_change_and_expert_when_read_only() -> None:
    """Read-only stdio launch must disable both 'change' and 'expert' tagged tools."""
    fake_settings = MagicMock()
    fake_settings.modal_mcp_enabled_toolsets = list(
        __import__("modal_mcp.server", fromlist=["ALL_TOOLSETS"]).ALL_TOOLSETS
    )
    fake_settings.modal_mcp_read_only = True

    fake_mcp_instance = MagicMock()

    with (
        patch("modal_mcp.server.FastMCP", return_value=fake_mcp_instance),
        patch("modal_mcp.server.register_toolsets"),
        patch("modal_mcp.server.configure_logging"),
    ):
        from modal_mcp.server import run_stdio

        run_stdio(fake_settings)

    # Inspect the disable() calls — read-only must disable change+expert.
    disabled_tag_sets = [
        call.kwargs.get("tags")
        for call in fake_mcp_instance.disable.call_args_list
        if "tags" in call.kwargs
    ]
    flat = set().union(*disabled_tag_sets) if disabled_tag_sets else set()
    assert "change" in flat, (
        f"'change' must be disabled on read-only stdio; got {flat!r}"
    )
    assert "expert" in flat, (
        f"'expert' must be disabled on read-only stdio; got {flat!r}"
    )
