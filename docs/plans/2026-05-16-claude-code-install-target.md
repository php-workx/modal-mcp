# Claude Code Install Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated install support for Claude Code (the `claude` CLI) so `modal-mcp setup --install claude-code` writes `~/.claude/settings.json` with the MCP server entry, matching the existing UX for `--install codex` and `--install claude` (Claude Desktop). Also fix a parallel correctness bug: the current Claude Code config example in `docs/clients.md` uses `args = ["run", ...]`, which after the stdio fix starts HTTP/Uvicorn — Claude Code spawns it over stdio and times out. Flip to `stdio`.

**Architecture:** Claude Code's config file (`~/.claude/settings.json`) shares the same JSON+`mcpServers` schema as Claude Desktop's `claude_desktop_config.json`. The only differences are the file path (platform-agnostic `~/.claude/settings.json` vs platform-specific Claude Desktop paths) and the transport (Claude Code launches `modal-mcp` over stdio; Claude Desktop uses HTTP/SSE). The new adapter mirrors `agent_targets/claude.py` for the file I/O and JSON merge logic, but with stdio transport and the simpler platform-agnostic path. Registers in `agent_targets/__init__.py::_TARGETS` as `"claude_code"` with `"claude-code"` alias so users can type either.

**Tech Stack:** Python 3.12, json (stdlib), pytest, ruff.

---

## File Structure

```text
src/modal_mcp/agent_targets/
    claude_code.py                       ← NEW: adapter mirroring claude.py
    __init__.py                          ← register "claude_code" + "claude-code" alias

tests/unit/
    test_agent_targets_claude_code.py    ← NEW: contract + install + render + install_from_cli tests
    test_agent_config_claude_code.py     ← NEW: print/install end-to-end tests

docs/
    clients.md                           ← update Claude Code section: stdio, mention --install claude-code
README.md                                ← update install table
```

---

## Step 1 — Write failing tests for the contract surface (RED)

- [ ] Create `tests/unit/test_agent_targets_claude_code.py`:

```python
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
            "idempotent install must not rewrite the file when the entry already matches"
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
```

- [ ] Run RED: `uv run pytest tests/unit/test_agent_targets_claude_code.py -v` — expect all FAIL (`ModuleNotFoundError: modal_mcp.agent_targets.claude_code`).

- [ ] Commit RED: `git commit -m "test(claude-code): add failing contract + install + render tests"`.

---

## Step 2 — Create the adapter module (GREEN)

- [ ] Create `src/modal_mcp/agent_targets/claude_code.py` by adapting `agent_targets/claude.py`. Key differences:

  - **Config path**: `Path("~/.claude/settings.json").expanduser()` directly. No platform-specific logic.
  - **Transport**: `mcp_transport="stdio"` in the contract; `server_command="modal-mcp"`; `server_url=None`; `server_args_template=("stdio", "--env-file", "{env_file}")`.
  - **InstallError**: define `ClaudeCodeInstallError(Exception)` and export `INSTALL_ERROR = ClaudeCodeInstallError`.
  - **Dry-run description**: e.g. `"add mcpServers.modal-mcp entry to ~/.claude/settings.json (stdio transport)"`.
  - **install_from_cli**: accept `args: argparse.Namespace` and an optional `config_path_override: Path | None = None` kw-only for tests. Resolve `env_file`, call `install(env_file=..., dry_run=args.dry_run, yes=args.yes, config_path_override=...)`. Catch `(ValueError, INSTALL_ERROR)` and return 1 with stderr message; success returns 0.

- [ ] Reuse `_atomic_write_text`, `_make_timestamp` from `domain/file_io.py` (recently extracted; available across all targets).

- [ ] Idempotency: compare existing JSON entry to the new one; if equal, skip write entirely (preserve mtime). This is the `test_install_is_idempotent_when_entry_matches` invariant.

- [ ] Post-write validation: `json.loads(config_path.read_text())` round-trip; restore from backup on failure.

- [ ] Run tests after each substep:
  ```bash
  cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_agent_targets_claude_code.py -v 2>&1 | tail -30
  ```

- [ ] Commit GREEN: `git commit -m "feat(claude-code): add agent target adapter for ~/.claude/settings.json"`.

---

## Step 3 — Register in the agent_targets registry

- [ ] Update `src/modal_mcp/agent_targets/__init__.py`:

```python
from modal_mcp.agent_targets import claude, claude_code, codex
# ...
_TARGETS: Final[dict[str, ModuleType]] = {
    "codex": codex,
    "claude": claude,
    "claude_desktop": claude,  # alias
    "claude_code": claude_code,
    "claude-code": claude_code,  # hyphen alias matching --install token
}
```

- [ ] Verify the CLI auto-picks it up — `cli/setup.py::--install` choices are derived from `TARGETS` (per PR #12). No edit needed there. Confirm:
  ```bash
  cd "$(git rev-parse --show-toplevel)" && uv run modal-mcp setup --help 2>&1 | grep -A 2 install
  ```
  Expected: `--install {claude,claude-code,claude_code,claude_desktop,codex}` (sorted).

- [ ] Verify `print-agent-config --target claude-code` works:
  ```bash
  cd "$(git rev-parse --show-toplevel)" && uv run modal-mcp print-agent-config --target claude-code 2>&1
  ```
  Expected: JSON snippet with `args = ["stdio", "--env-file", "/absolute/path/to/.env"]`.

- [ ] Add `"claude_code"` and `"claude-code"` to the `--target` choices in `cli/print_agent_config.py` (if it has explicit choices; otherwise derive from registry like setup does).

- [ ] Commit: `git commit -m "feat(agent-targets): register claude_code in registry with hyphen alias"`.

---

## Step 4 — End-to-end contract tests

- [ ] Create `tests/unit/test_agent_config_claude_code.py` mirroring the structure of `tests/unit/test_agent_config_codex.py`. Cover:

  - `print_agent_config("claude_code")` and `print_agent_config("claude-code")` both produce the same stdio snippet.
  - `print_agent_config("claude_code")` does NOT touch the filesystem (negative file-create test).
  - `install_from_cli` end-to-end with a fake settings.json path: write → verify JSON → run again (idempotent).
  - Path-traversal-style malformed `--env-file` is rejected (non-absolute path).
  - Dry-run prints the change description and does NOT write.

- [ ] Run: `cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_agent_config_claude_code.py -v 2>&1 | tail -30`.

- [ ] Commit: `git commit -m "test(claude-code): end-to-end print + install + dry-run contract tests"`.

---

## Step 5 — Fix the parallel docs bug + document the new install path

- [ ] In `docs/clients.md` Claude Code section (~lines 25-78), make these changes:

  - Add an opening line right after the heading: *"Recommended: `modal-mcp setup --install claude-code` writes `~/.claude/settings.json` for you with backup, idempotency check, and dry-run support. The manual snippet below is the fallback for operators who prefer hand-editing."*

  - Flip `args = ["run", "--env-file", ...]` to `args = ["stdio", "--env-file", ...]` in all three code blocks (canonical, absolute-exe fallback, `uv run` fallback). This fixes the parallel regression that Codex had until PR #12.

  - Add a `--dry-run` example showing what the install would write.

- [ ] In `README.md` install table, update the Claude Code row:
  ```
  | Claude Code install | Supported by `modal-mcp setup --install claude-code` |
  ```

- [ ] Commit: `git commit -m "docs(clients): claude-code uses stdio transport + automated install"`.

---

## Step 6 — Final lint + format + full pytest + smoke test

- [ ] `cd "$(git rev-parse --show-toplevel)" && uv run ruff check . --fix && uv run ruff format src tests scripts`.

- [ ] `cd "$(git rev-parse --show-toplevel)" && uv run pytest 2>&1 | tail -20`. Expected: all tests pass, new claude_code suite added.

- [ ] `cd "$(git rev-parse --show-toplevel)" && just pre-push 2>&1 | tail -15` if available. Expected: green.

- [ ] Smoke test the install (dry-run, no file written):
  ```bash
  cd "$(git rev-parse --show-toplevel)" && uv run modal-mcp setup --install claude-code --dry-run --env-file /tmp/example.env 2>&1
  ```
  Expected: human-readable description of the change, no filesystem write.

- [ ] Smoke test the schema export:
  ```bash
  cd "$(git rev-parse --show-toplevel)" && uv run modal-mcp print-agent-config --target claude-code 2>&1 | python3 -m json.tool
  ```
  Expected: valid JSON with `args: ["stdio", "--env-file", "..."]`.

- [ ] If a cleanup commit is needed (formatting, etc.), make it a final small commit: `git commit -m "chore(claude-code): lint+format cleanup"`.

---

## Self-review checklist

| Spec item | Step | Evidence |
|---|---|---|
| `--install claude-code` writes `~/.claude/settings.json` | Step 2, 6 smoke | Adapter `install` function + smoke test |
| Stdio transport (not HTTP) | Steps 2, 5 | `server_args_template[0] == "stdio"`, docs snippet flipped |
| Hyphen alias accepted | Step 3 | Registry entry, CLI `--install` choices |
| Idempotent re-install | Step 2 | `test_install_is_idempotent_when_entry_matches` |
| Backup on overwrite | Step 2 | `test_install_creates_backup_when_replacing` |
| Dry-run does not write | Step 2 | `test_install_dry_run_does_not_write` |
| Other MCP servers preserved | Step 2 | `test_install_preserves_other_mcp_servers` |
| Absolute env-file path enforced | Step 2 | `test_render_rejects_non_absolute_env_file` |
| Docs match implementation | Step 5 | clients.md + README.md edits |
| Full test suite green | Step 6 | `uv run pytest` |
