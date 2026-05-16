"""End-to-end tests for the claude_code agent target via the registry/CLI.

Mirrors the structure of ``tests/unit/test_agent_config_codex.py`` but for the
Claude Code adapter at ``~/.claude/settings.json``.

Covered surface:

- ``get_target("claude_code")`` and the hyphen alias ``get_target("claude-code")``
  resolve to the same module and produce identical render output.
- ``print_agent_config`` does not write any files.
- ``install`` end-to-end: write → JSON-valid → idempotent re-run.
- Malformed ``--env-file`` (non-absolute path) is rejected by both
  ``render`` and ``install``.
- Dry-run prints the change description and does not touch the filesystem.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any

import pytest

from modal_mcp.agent_targets import claude_code, get_target
from modal_mcp.agent_targets.claude_code import (
    CLAUDE_CODE_SERVER_ARGS_TEMPLATE,
    CLAUDE_CODE_SERVER_NAME,
    CLAUDE_CODE_TOP_LEVEL_KEY,
)


def render_via_registry(target: str, **kwargs: Any) -> str:
    """Test helper invoking ``get_target(target).render(**kwargs)``."""
    return get_target(target).render(**kwargs)


# ---------------------------------------------------------------------------
# Registry + alias parity
# ---------------------------------------------------------------------------


def test_underscore_and_hyphen_aliases_resolve_to_same_module() -> None:
    assert get_target("claude_code") is get_target("claude-code")
    assert get_target("claude_code") is claude_code


def test_aliases_produce_identical_render_output(tmp_path: Path) -> None:
    env = tmp_path / "env"
    buf_under = io.StringIO()
    buf_hyphen = io.StringIO()
    render_via_registry("claude_code", env_file=env, file=buf_under)
    render_via_registry("claude-code", env_file=env, file=buf_hyphen)
    assert buf_under.getvalue() == buf_hyphen.getvalue()


def test_render_output_is_valid_json_for_both_aliases(tmp_path: Path) -> None:
    env = tmp_path / "env"
    for alias in ("claude_code", "claude-code"):
        buf = io.StringIO()
        render_via_registry(alias, env_file=env, file=buf)
        parsed = json.loads(buf.getvalue())
        assert CLAUDE_CODE_TOP_LEVEL_KEY in parsed
        entry = parsed[CLAUDE_CODE_TOP_LEVEL_KEY][CLAUDE_CODE_SERVER_NAME]
        assert entry["command"] == "modal-mcp"
        assert entry["args"][0] == CLAUDE_CODE_SERVER_ARGS_TEMPLATE[0] == "stdio"
        assert "--env-file" in entry["args"]
        assert str(env) in entry["args"]


# ---------------------------------------------------------------------------
# print-agent-config does NOT touch the filesystem
# ---------------------------------------------------------------------------


def test_render_does_not_write_files(tmp_path: Path) -> None:
    before = set(tmp_path.iterdir())
    buf = io.StringIO()
    render_via_registry("claude_code", file=buf)
    after = set(tmp_path.iterdir())
    assert before == after, (
        f"render must not create files; new entries: {after - before}"
    )


def test_render_with_no_env_file_uses_absolute_placeholder() -> None:
    buf = io.StringIO()
    render_via_registry("claude_code", file=buf)
    parsed = json.loads(buf.getvalue())
    args = parsed[CLAUDE_CODE_TOP_LEVEL_KEY][CLAUDE_CODE_SERVER_NAME]["args"]
    # The placeholder must start with / so users see it is an absolute path.
    env_arg = args[-1]
    assert env_arg.startswith("/"), (
        f"placeholder env-file path must be absolute; got {env_arg!r}"
    )


# ---------------------------------------------------------------------------
# Install end-to-end: write → valid JSON → idempotent re-run
# ---------------------------------------------------------------------------


def test_install_end_to_end_writes_valid_json(tmp_path: Path) -> None:
    config = tmp_path / "settings.json"
    env_file = tmp_path / ".env"
    env_file.write_text("X=1\n", encoding="utf-8")
    first = claude_code.install(
        env_file=str(env_file),
        yes=True,
        config_path_override=config,
    )
    assert first == "installed"
    data = json.loads(config.read_text(encoding="utf-8"))
    entry = data[CLAUDE_CODE_TOP_LEVEL_KEY][CLAUDE_CODE_SERVER_NAME]
    assert entry["command"] == "modal-mcp"
    assert entry["args"] == ["stdio", "--env-file", str(env_file)]


def test_install_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "settings.json"
    env_file = tmp_path / ".env"
    env_file.write_text("X=1\n", encoding="utf-8")
    first = claude_code.install(
        env_file=str(env_file), yes=True, config_path_override=config
    )
    second = claude_code.install(
        env_file=str(env_file), yes=True, config_path_override=config
    )
    assert first == "installed"
    assert second == "already_installed"


def test_install_dry_run_prints_description_and_does_not_write(
    tmp_path: Path,
) -> None:
    config = tmp_path / "settings.json"
    env_file = tmp_path / ".env"
    buf = io.StringIO()
    result = claude_code.install(
        env_file=str(env_file),
        dry_run=True,
        yes=True,
        config_path_override=config,
        file=buf,
    )
    assert result == "dry_run"
    assert not config.exists()
    output = buf.getvalue()
    # Description mentions the target path and the planned change.
    assert str(config) in output
    assert "mcpServers.modal-mcp" in output
    assert "stdio" in output.lower()


# ---------------------------------------------------------------------------
# Malformed env-file rejection
# ---------------------------------------------------------------------------


def test_render_rejects_relative_env_file() -> None:
    with pytest.raises(ValueError, match="absolute"):
        render_via_registry("claude_code", env_file="relative/.env", file=io.StringIO())


def test_install_rejects_relative_env_file(tmp_path: Path) -> None:
    config = tmp_path / "settings.json"
    with pytest.raises(ValueError, match="absolute"):
        claude_code.install(
            env_file="relative/.env",
            yes=True,
            config_path_override=config,
        )


def test_install_from_cli_rejects_relative_env_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(
        env_file="relative/.env",
        dry_run=True,
        yes=True,
        install="claude-code",
    )
    rc = claude_code.install_from_cli(
        args, config_path_override=tmp_path / "settings.json"
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "absolute" in err.lower()


# ---------------------------------------------------------------------------
# install_from_cli wiring: dry-run path
# ---------------------------------------------------------------------------


def test_install_from_cli_dry_run_returns_zero(tmp_path: Path) -> None:
    config = tmp_path / "settings.json"
    env_file = tmp_path / ".env"
    env_file.write_text("X=1\n", encoding="utf-8")
    args = argparse.Namespace(
        env_file=str(env_file),
        dry_run=True,
        yes=True,
        install="claude-code",
    )
    rc = claude_code.install_from_cli(args, config_path_override=config)
    assert rc == 0
    assert not config.exists()


def test_install_from_cli_install_writes_and_returns_zero(tmp_path: Path) -> None:
    config = tmp_path / "settings.json"
    env_file = tmp_path / ".env"
    env_file.write_text("X=1\n", encoding="utf-8")
    args = argparse.Namespace(
        env_file=str(env_file),
        dry_run=False,
        yes=True,
        install="claude-code",
    )
    rc = claude_code.install_from_cli(args, config_path_override=config)
    assert rc == 0
    data = json.loads(config.read_text(encoding="utf-8"))
    assert (
        data[CLAUDE_CODE_TOP_LEVEL_KEY][CLAUDE_CODE_SERVER_NAME]["args"][0] == "stdio"
    )


# ---------------------------------------------------------------------------
# Sanity: render output contains no secret-looking patterns
# ---------------------------------------------------------------------------


def test_render_does_not_leak_secrets() -> None:
    buf = io.StringIO()
    render_via_registry("claude_code", file=buf)
    output_lower = buf.getvalue().lower()
    for pattern in ("password", "token", "api_key", "secret", "credential"):
        assert pattern not in output_lower, f"unexpected secret-like word: {pattern!r}"
