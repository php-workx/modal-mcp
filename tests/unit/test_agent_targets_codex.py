"""Tests for the codex agent target adapter (install + render absorbed)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


def test_codex_target_exposes_install_and_render() -> None:
    from modal_mcp.agent_targets import codex

    assert callable(codex.install)
    assert callable(codex.render)


def test_codex_target_install_signature_keyword_only() -> None:
    """install() must require env_file as a keyword argument."""
    from modal_mcp.agent_targets import codex

    sig = inspect.signature(codex.install)
    assert "env_file" in sig.parameters
    assert sig.parameters["env_file"].kind == inspect.Parameter.KEYWORD_ONLY


def test_codex_render_returns_snippet_with_block_header(tmp_path: Path) -> None:
    from modal_mcp.agent_targets import codex

    env_file = tmp_path / ".env"
    out = codex.render(env_file=env_file)
    assert "[mcp_servers.modal-mcp]" in out
    assert str(env_file) in out


def test_codex_render_relative_env_file_raises() -> None:
    from modal_mcp.agent_targets import codex

    with pytest.raises(ValueError, match="absolute path"):
        codex.render(env_file=Path(".env"))


def test_codex_install_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    from modal_mcp.agent_targets import codex

    target = tmp_path / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    env_file = tmp_path / ".env"
    result = codex.install(
        env_file=env_file,
        dry_run=True,
        yes=True,
        config_path=target,
    )
    assert result == "dry_run"
    assert not target.exists()
    captured = capsys.readouterr()
    assert "would add" in captured.out.lower()


def test_codex_install_idempotent(tmp_path: Path) -> None:
    from modal_mcp.agent_targets import codex

    target = tmp_path / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    env_file = tmp_path / ".env"
    env_file.write_text("# placeholder\n", encoding="utf-8")

    first = codex.install(env_file=env_file, yes=True, config_path=target)
    second = codex.install(env_file=env_file, yes=True, config_path=target)
    assert first == "installed"
    assert second == "already_installed"
