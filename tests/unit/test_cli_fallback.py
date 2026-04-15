"""Unit tests for the disabled Modal CLI fallback adapter."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def test_default_import_tree_does_not_pull_in_cli_fallback() -> None:
    """Core package imports should not reach the dead fallback module."""

    sys.modules.pop("modal_mcp.adapters._cli_fallback", None)

    import modal_mcp as modal_pkg
    import modal_mcp.asgi as asgi_mod
    import modal_mcp.auth as auth_mod
    import modal_mcp.config as config_mod

    del modal_pkg, asgi_mod, auth_mod, config_mod

    assert "modal_mcp.adapters._cli_fallback" not in sys.modules

    module = importlib.import_module("modal_mcp.adapters._cli_fallback")

    assert module.__name__ == "modal_mcp.adapters._cli_fallback"


def test_cli_fallback_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fallback stays off unless explicitly enabled."""

    monkeypatch.delenv("MODAL_MCP_CLI_FALLBACK", raising=False)

    from modal_mcp.adapters._cli_fallback import (
        CliFallbackDisabledError,
        is_cli_fallback_enabled,
        require_cli_fallback_enabled,
    )

    assert is_cli_fallback_enabled() is False
    with pytest.raises(CliFallbackDisabledError, match="disabled by default"):
        require_cli_fallback_enabled()


def test_cli_fallback_enables_only_for_explicit_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an explicit true value enables the fallback code path."""

    monkeypatch.setenv("MODAL_MCP_CLI_FALLBACK", "true")

    from modal_mcp.adapters._cli_fallback import is_cli_fallback_enabled

    assert is_cli_fallback_enabled() is True


def test_cli_fallback_run_enforces_allowlist_timeout_env_redaction_and_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The subprocess wrapper stays shell-free and strips unsafe state."""

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("UNRELATED", "should-not-leak")
    monkeypatch.setenv("MODAL_MCP_CLI_FALLBACK", "true")

    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="prefix SECRET-MATERIAL " + "x" * 32,
            stderr="stderr SECRET-MATERIAL " + "y" * 32,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    from modal_mcp.adapters._cli_fallback import (
        CliFallbackContext,
        run_modal_cli,
    )

    result = run_modal_cli(
        ["modal", "apps", "list", "--json"],
        context=CliFallbackContext(
            modal_token_id="token-id",
            modal_token_secret="token-secret",
            modal_config_path="/tmp/modal.toml",
            modal_environment="prod",
        ),
        timeout_seconds=2.5,
        max_output_chars=24,
        redact_values=("SECRET-MATERIAL",),
    )

    assert captured["args"] == ["modal", "apps", "list", "--json"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 2.5
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["env"] == {
        "PATH": "/usr/bin",
        "HOME": "/home/tester",
        "LANG": "C.UTF-8",
        "MODAL_TOKEN_ID": "token-id",
        "MODAL_TOKEN_SECRET": "token-secret",
        "MODAL_CONFIG_PATH": "/tmp/modal.toml",
        "MODAL_ENVIRONMENT": "prod",
    }
    assert "UNRELATED" not in captured["kwargs"]["env"]
    assert result.stdout.startswith("prefix [REDACTED]")
    assert result.stderr.startswith("stderr [REDACTED]")
    assert "SECRET-MATERIAL" not in result.stdout
    assert "SECRET-MATERIAL" not in result.stderr
    assert len(result.stdout) == 24
    assert len(result.stderr) == 24


def test_cli_fallback_rejects_unallowlisted_subcommands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only explicit Modal CLI subcommands are allowed."""

    monkeypatch.setenv("MODAL_MCP_CLI_FALLBACK", "true")

    from modal_mcp.adapters._cli_fallback import (
        CliFallbackCommandError,
        CliFallbackContext,
        run_modal_cli,
    )

    with pytest.raises(CliFallbackCommandError, match="not allowlisted"):
        run_modal_cli(
            ["modal", "sh", "-c", "id"],
            context=CliFallbackContext(
                modal_token_id="token-id",
                modal_token_secret="token-secret",
                modal_config_path="/tmp/modal.toml",
            ),
        )
