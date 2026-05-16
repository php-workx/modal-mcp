"""Contract + behaviour tests for the claude_code agent target."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any

import pytest

from modal_mcp.agent_targets import claude_code, get_target
from modal_mcp.agent_targets.contract import AgentTargetContract


class TestClaudeCodeContract:
    def test_exports_install_render_install_from_cli(self) -> None:
        assert callable(claude_code.install)
        assert callable(claude_code.render)
        assert callable(claude_code.install_from_cli)

    def test_exposes_install_error_constant(self) -> None:
        assert hasattr(claude_code, "INSTALL_ERROR")
        assert issubclass(claude_code.INSTALL_ERROR, Exception)

    def test_contract_fields(self) -> None:
        contract = claude_code.CONTRACT
        assert isinstance(contract, AgentTargetContract)
        assert contract.agent_name == "claude_code"
        assert contract.config_format == "json"
        assert contract.mcp_transport == "stdio"
        assert contract.server_name == "modal-mcp"
        assert contract.top_level_key == "mcpServers"
        assert contract.server_command == "modal-mcp"
        assert contract.server_args_template[0] == "stdio"
        assert contract.env_file_strategy == "absolute_path_flag"

    def test_representative_path_is_dot_claude_settings(self) -> None:
        # NOT ~/.config/claude/...; Claude Code uses ~/.claude/settings.json.
        assert claude_code.CONTRACT.representative_config_path == Path(
            "~/.claude/settings.json"
        )

    def test_registered_under_claude_code_and_alias(self) -> None:
        assert get_target("claude_code") is claude_code
        assert get_target("claude-code") is claude_code  # hyphen alias


class TestClaudeCodeRender:
    def test_render_writes_stdio_snippet_with_env_file(self) -> None:
        buf = io.StringIO()
        claude_code.render(env_file="/abs/path/to/.env", file=buf)
        out = buf.getvalue()
        parsed: dict[str, Any] = json.loads(out)
        entry = parsed["mcpServers"]["modal-mcp"]
        assert entry["command"] == "modal-mcp"
        assert entry["args"] == ["stdio", "--env-file", "/abs/path/to/.env"]

    def test_render_rejects_non_absolute_env_file(self) -> None:
        buf = io.StringIO()
        with pytest.raises(ValueError, match="absolute"):
            claude_code.render(env_file="relative/.env", file=buf)


class TestClaudeCodeInstall:
    def test_install_creates_settings_json_when_missing(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        claude_code.install(
            env_file=str(tmp_path / "env"),
            dry_run=False,
            yes=True,
            config_path_override=config,
        )
        # absolute path enforcement: env file path must be absolute
        # use tmp_path which is absolute
        assert config.exists()
        data = json.loads(config.read_text(encoding="utf-8"))
        assert data["mcpServers"]["modal-mcp"]["args"][0] == "stdio"
        assert data["mcpServers"]["modal-mcp"]["command"] == "modal-mcp"

    def test_install_dry_run_does_not_write(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        claude_code.install(
            env_file=str(tmp_path / "env"),
            dry_run=True,
            yes=True,
            config_path_override=config,
        )
        assert not config.exists()

    def test_install_preserves_other_mcp_servers(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        existing = {
            "mcpServers": {
                "some-other": {"command": "other", "args": []},
            },
            "unrelated": {"keep": True},
        }
        config.write_text(json.dumps(existing), encoding="utf-8")
        claude_code.install(
            env_file=str(tmp_path / "env"),
            dry_run=False,
            yes=True,
            config_path_override=config,
        )
        data = json.loads(config.read_text(encoding="utf-8"))
        assert "some-other" in data["mcpServers"]
        assert "modal-mcp" in data["mcpServers"]
        assert data["unrelated"] == {"keep": True}

    def test_install_is_idempotent_when_entry_matches(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        # First install
        claude_code.install(
            env_file=str(tmp_path / "env"),
            dry_run=False,
            yes=True,
            config_path_override=config,
        )
        mtime_first = config.stat().st_mtime
        # Second install with identical args should not modify the file
        claude_code.install(
            env_file=str(tmp_path / "env"),
            dry_run=False,
            yes=True,
            config_path_override=config,
        )
        mtime_second = config.stat().st_mtime
        assert mtime_first == mtime_second, (
            "idempotent install must not rewrite when the entry already matches"
        )

    def test_install_creates_backup_when_replacing(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        existing = {"mcpServers": {"modal-mcp": {"command": "old", "args": []}}}
        config.write_text(json.dumps(existing), encoding="utf-8")
        claude_code.install(
            env_file=str(tmp_path / "env"),
            dry_run=False,
            yes=True,
            config_path_override=config,
        )
        backups = list(tmp_path.glob("settings.json.bak.*"))
        assert backups, "install must back up the existing file before overwriting"


class TestClaudeCodeInstallFromCli:
    def test_install_from_cli_invokes_install_with_default_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
        config = tmp_path / "settings.json"
        args = argparse.Namespace(
            env_file=None, dry_run=True, yes=True, install="claude_code"
        )
        # Need to override path; install_from_cli should accept this.
        # If the signature does not allow override, the contract tests above
        # will catch it during implementation.
        rc = claude_code.install_from_cli(args, config_path_override=config)
        assert rc == 0
