"""Contract and behaviour tests for the claude_code agent target."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from modal_mcp.agent_targets import claude_code, get_target
from modal_mcp.agent_targets.contract import AgentTargetContract

# ---------------------------------------------------------------------------
# Contract surface
# ---------------------------------------------------------------------------


class TestClaudeCodeContract:
    def test_exports_install_render_install_from_cli(self) -> None:
        assert callable(claude_code.install)
        assert callable(claude_code.render)
        assert callable(claude_code.install_from_cli)

    def test_exposes_install_error_constant(self) -> None:
        assert hasattr(claude_code, "INSTALL_ERROR")
        assert issubclass(claude_code.INSTALL_ERROR, Exception)

    def test_contract_fields(self) -> None:
        c = claude_code.CONTRACT
        assert isinstance(c, AgentTargetContract)
        assert c.agent_name == "claude_code"
        assert c.config_format == "json"
        assert c.mcp_transport == "stdio"
        assert c.server_name == "modal-mcp"
        assert c.top_level_key == "mcpServers"
        assert c.server_command == "modal-mcp"
        assert c.server_args_template[0] == "stdio"
        assert c.env_file_strategy == "absolute_path_flag"

    def test_representative_path_is_dot_claude_json(self) -> None:
        # Claude Code MCP config lives in ~/.claude.json, NOT ~/.claude/settings.json.
        assert claude_code.CONTRACT.representative_config_path == Path("~/.claude.json")

    def test_registered_under_claude_code_and_alias(self) -> None:
        assert get_target("claude_code") is claude_code
        assert get_target("claude-code") is claude_code


# ---------------------------------------------------------------------------
# render() — prints the `claude mcp add-json` command, not raw JSON
# ---------------------------------------------------------------------------


class TestClaudeCodeRender:
    def test_render_prints_add_json_command(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude_code.render(env_file="/abs/path/.env", file=None)
        captured = capsys.readouterr()
        assert "claude mcp add-json" in captured.out
        assert "modal-mcp" in captured.out
        assert "stdio" in captured.out
        assert "/abs/path/.env" in captured.out

    def test_render_to_file(self, tmp_path: Path) -> None:
        import io

        buf = io.StringIO()
        claude_code.render(env_file="/abs/path/.env", file=buf)
        out = buf.getvalue()
        assert "claude mcp add-json" in out
        assert "--scope user" in out

    def test_render_respects_scope(self) -> None:
        import io

        buf = io.StringIO()
        claude_code.render(env_file="/abs/path/.env", scope="project", file=buf)
        assert "--scope project" in buf.getvalue()

    def test_render_rejects_non_absolute_env_file(self) -> None:
        import io

        with pytest.raises(ValueError, match="absolute"):
            claude_code.render(env_file="relative/.env", file=io.StringIO())


# ---------------------------------------------------------------------------
# install() — delegates to subprocess calls
# ---------------------------------------------------------------------------


class TestClaudeCodeInstall:
    def _make_completed(
        self, returncode: int = 0, stdout: str = "", stderr: str = ""
    ) -> MagicMock:
        m = MagicMock()
        m.returncode = returncode
        m.stdout = stdout
        m.stderr = stderr
        return m

    def test_install_calls_add_json_when_not_present(self, tmp_path: Path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{}", encoding="utf-8")

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = self._make_completed(0)
            result = claude_code.install(
                env_file="/abs/.env",
                dry_run=False,
                yes=True,
                scope="user",
                _claude_json_path=claude_json,
            )

        assert result == "installed"
        args_list = mock_run.call_args[0][0]
        assert args_list[0] == "claude"
        assert "add-json" in args_list
        assert "modal-mcp" in args_list

    def test_install_dry_run_does_not_call_subprocess(self, tmp_path: Path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{}", encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            result = claude_code.install(
                env_file="/abs/.env",
                dry_run=True,
                yes=True,
                scope="user",
                _claude_json_path=claude_json,
            )

        mock_run.assert_not_called()
        assert result == "dry_run"

    def test_install_is_idempotent_when_entry_matches(self, tmp_path: Path) -> None:
        entry = {
            "type": "stdio",
            "command": "modal-mcp",
            "args": ["stdio", "--env-file", "/abs/.env"],
        }
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(
            json.dumps({"mcpServers": {"modal-mcp": entry}}), encoding="utf-8"
        )

        with patch("subprocess.run") as mock_run:
            result = claude_code.install(
                env_file="/abs/.env",
                dry_run=False,
                yes=True,
                scope="user",
                _claude_json_path=claude_json,
            )

        mock_run.assert_not_called()
        assert result == "already_installed"

    def test_install_replaces_when_entry_differs(self, tmp_path: Path) -> None:
        old_entry = {"command": "modal-mcp", "args": ["run", "--env-file", "/old/.env"]}
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(
            json.dumps({"mcpServers": {"modal-mcp": old_entry}}), encoding="utf-8"
        )

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = self._make_completed(0)
            result = claude_code.install(
                env_file="/abs/.env",
                dry_run=False,
                yes=True,
                scope="user",
                _claude_json_path=claude_json,
            )

        # Expect: remove then add-json
        calls = [str(c) for c in mock_run.call_args_list]
        assert any("remove" in c for c in calls)
        assert any("add-json" in c for c in calls)
        assert result == "installed"

    def test_install_errors_when_claude_cli_missing(self, tmp_path: Path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{}", encoding="utf-8")

        with (
            patch("shutil.which", return_value=None),
            pytest.raises(claude_code.INSTALL_ERROR, match=r"claude.*not found"),
        ):
            claude_code.install(
                env_file="/abs/.env",
                dry_run=False,
                yes=True,
                scope="user",
                _claude_json_path=claude_json,
            )

    def test_install_rejects_non_absolute_env_file(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="absolute"):
            claude_code.install(
                env_file="relative/.env",
                dry_run=False,
                yes=True,
                scope="user",
            )


class TestClaudeCodeInstallFromCli:
    def test_install_from_cli_returns_0_on_success(self, tmp_path: Path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{}", encoding="utf-8")
        (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
        args = argparse.Namespace(
            env_file=str(tmp_path / ".env"),
            dry_run=True,
            yes=True,
            install="claude_code",
            scope="user",
        )
        rc = claude_code.install_from_cli(args, _claude_json_path=claude_json)
        assert rc == 0
