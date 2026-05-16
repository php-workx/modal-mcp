"""Tests for the claude_desktop agent target adapter (install + render absorbed)."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest


def test_claude_target_exposes_install_and_render() -> None:
    from modal_mcp.agent_targets import claude

    assert callable(claude.install)
    assert callable(claude.render)


def test_claude_target_install_signature_keyword_only() -> None:
    """install() must take keyword-only arguments."""
    from modal_mcp.agent_targets import claude

    sig = inspect.signature(claude.install)
    # All install params should be keyword-only
    for name in ("bind", "dry_run", "yes", "config_path"):
        assert name in sig.parameters
        assert sig.parameters[name].kind == inspect.Parameter.KEYWORD_ONLY


def test_claude_render_returns_sse_snippet() -> None:
    from modal_mcp.agent_targets import claude

    out = claude.render()
    assert "mcpServers" in out
    assert "modal-mcp" in out
    assert "sse" in out


def test_claude_install_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    from modal_mcp.agent_targets import claude

    target = tmp_path / "claude_desktop_config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = claude.install(
        dry_run=True,
        yes=True,
        config_path=target,
    )
    assert result == "dry_run"
    assert not target.exists()
    captured = capsys.readouterr()
    assert "would add" in captured.out.lower()


def test_claude_install_writes_valid_json(tmp_path: Path) -> None:
    from modal_mcp.agent_targets import claude

    target = tmp_path / "claude_desktop_config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = claude.install(yes=True, config_path=target)
    assert result == "installed"
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["mcpServers"]["modal-mcp"]["type"] == "sse"


def test_claude_render_raises_value_error_on_relative_env_file() -> None:
    from modal_mcp.agent_targets import claude

    with pytest.raises(ValueError, match="absolute path"):
        claude.render(env_file=Path(".env"))
