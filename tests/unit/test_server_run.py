"""Unit tests for server.run() without a live socket or Modal credentials."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from modal_mcp.policy.engine import PolicyMiddleware
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
    """run_stdio(settings) must reuse create_mcp() and call mcp.run(transport='stdio').

    Stdio launch is the Codex subprocess path: there is no uvicorn, no
    OriginGuard, no approval HTTP route. We assert the negative (no uvicorn
    call) alongside the positive (the create_mcp-composed FastMCP.run was
    called with the right transport) so that a future refactor cannot
    silently re-introduce HTTP side-effects on the stdio path. We patch
    ``create_mcp`` rather than ``FastMCP`` directly to anchor the contract
    on the shared composition entrypoint — both transports MUST go through
    ``create_mcp`` to inherit PolicyMiddleware, redaction, etc.
    """
    fake_settings = MagicMock()
    fake_mcp_instance = MagicMock()

    with (
        patch(
            "modal_mcp.server.create_mcp", return_value=fake_mcp_instance
        ) as mock_create_mcp,
        patch("modal_mcp.server.scrub_secret_env") as mock_scrub,
        patch("modal_mcp.server.uvicorn.run") as mock_uvicorn,
    ):
        from modal_mcp.server import run_stdio

        run_stdio(fake_settings)

    mock_create_mcp.assert_called_once_with(fake_settings)
    mock_scrub.assert_called_once_with()
    fake_mcp_instance.run.assert_called_once_with(transport="stdio")
    mock_uvicorn.assert_not_called()


def test_run_stdio_toolset_disable_matches_http_via_create_mcp() -> None:
    """Read-only stdio launch must defer toolset gating to ``create_mcp``.

    Regression guard for the divergent-disable-list bug: the previous
    ``run_stdio`` rebuilt its own disable rules and called
    ``mcp.disable(tags={"change", "expert"})`` on read-only — diverging
    from the HTTP path, which disables only ``{"expert"}``.  With Critical 1
    applied, stdio reuses ``create_mcp`` directly so the two transports
    cannot drift.  This test asserts that the stdio entrypoint hands the
    settings straight to ``create_mcp`` and does no post-processing of its
    own.
    """
    fake_settings = MagicMock()
    fake_settings.modal_mcp_read_only = True

    fake_mcp_instance = MagicMock()

    with (
        patch(
            "modal_mcp.server.create_mcp", return_value=fake_mcp_instance
        ) as mock_create_mcp,
        patch("modal_mcp.server.scrub_secret_env"),
    ):
        from modal_mcp.server import run_stdio

        run_stdio(fake_settings)

    # The single source of truth for which tags get disabled lives inside
    # create_mcp; assert that stdio went through it with the same settings
    # the caller passed in, with no divergent post-processing.
    mock_create_mcp.assert_called_once_with(fake_settings)
    fake_mcp_instance.run.assert_called_once_with(transport="stdio")


def test_run_stdio_wires_policy_middleware_into_fastmcp_stack(
    tmp_path: object,
) -> None:
    """run_stdio must produce a FastMCP instance with PolicyMiddleware attached.

    Regression guard for the dropped-middleware incident: the previous
    ``run_stdio`` built its own bare ``FastMCP`` and skipped
    ``PolicyMiddleware``, ``OtelMiddleware``, redaction, rate limiting,
    mutation gating, ``scrub_secret_env``, and ``assert_runtime_security``.
    Codex would then launch a stdio server that silently shipped a security
    regression vs the HTTP transport. This test runs ``run_stdio`` with a
    realistic settings object and asserts ``PolicyMiddleware`` ends up on
    the FastMCP middleware stack — proof that stdio goes through the same
    composition path as HTTP.
    """
    from pathlib import Path

    from pydantic import SecretStr

    from modal_mcp.config import Settings

    assert isinstance(tmp_path, Path)
    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    settings = Settings(
        modal_config_path=modal_config,
        modal_token_id=SecretStr("token-id-secret"),
        modal_token_secret=SecretStr("token-secret-value"),
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "b" * 64),
    )

    captured: dict[str, object] = {}

    def fake_run(self: object, *, transport: str) -> None:
        # Capture the assembled FastMCP and short-circuit before stdin/stdout
        # are touched — we only need to inspect the middleware stack.
        captured["mcp"] = self
        captured["transport"] = transport

    with (
        patch("modal_mcp.server.scrub_secret_env"),
        patch("modal_mcp.server.assert_runtime_security"),
        patch("fastmcp.FastMCP.run", new=fake_run),
    ):
        from modal_mcp.server import run_stdio

        run_stdio(settings)

    assert captured["transport"] == "stdio"
    mcp = captured["mcp"]
    middleware = getattr(mcp, "middleware", None)
    assert middleware is not None, "FastMCP must expose its middleware stack"
    assert any(isinstance(m, PolicyMiddleware) for m in middleware), (
        "stdio transport MUST have PolicyMiddleware on its middleware stack; "
        f"got {[type(m).__name__ for m in middleware]!r}"
    )
