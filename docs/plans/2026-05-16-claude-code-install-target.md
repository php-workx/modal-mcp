# Claude Code Install Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `modal-mcp setup --install claude-code` which runs `claude mcp add-json
modal-mcp '<json>' --scope <scope>` to register the MCP server in Claude Code's own
config (user scope by default). Also fix a correctness bug: `docs/clients.md` Claude Code
snippets use `args: ["run", ...]` which starts HTTP/Uvicorn — Claude Code expects stdio
and times out. Flip to `stdio` everywhere.

---

## Architecture decision (binding)

**Claude Code stores MCP servers in `~/.claude.json`, NOT `~/.claude/settings.json`.**
`settings.json` holds hooks/theme/permissions; writing `mcpServers` there is silently
ignored. Project scope uses `.mcp.json` at project root. The canonical write mechanism
is `claude mcp add-json <name> '<json>' [--scope user|project|local]`.

This adapter **delegates to the `claude` CLI** rather than writing JSON directly, for
two reasons:
1. `~/.claude.json` is a large opaque file owned by Claude Code; merging into it directly
   carries high risk of stomping unrelated state.
2. Delegating means we're immune to future schema changes in `~/.claude.json`.

All other adapters (codex, claude) write config files directly. This adapter is
intentionally different. **Record this in `docs/adr/0001-claude-code-delegates-to-cli.md`
(Step 5 creates it).**

---

## File structure

```text
src/modal_mcp/agent_targets/
    claude_code.py                       ← NEW: subprocess-delegation adapter
    __init__.py                          ← register "claude_code" + "claude-code" alias

src/modal_mcp/cli/
    setup.py                             ← add --scope flag (used by claude-code only)
    print_agent_config.py                ← add "claude_code"/"claude-code" to --target choices

tests/unit/
    test_agent_targets_claude_code.py    ← NEW: contract + subprocess mock tests

docs/
    adr/0001-claude-code-delegates-to-cli.md  ← NEW: decision record
    clients.md                           ← fix run→stdio, add --install example
README.md                                ← update install table
```

---

## Step 1 — Write failing tests (RED)

- [ ] Create `tests/unit/test_agent_targets_claude_code.py`:

```python
"""Contract and behaviour tests for the claude_code agent target."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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
    def test_render_prints_add_json_command(self, capsys: pytest.CaptureFixture[str]) -> None:
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
    def _make_completed(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
        m = MagicMock()
        m.returncode = returncode
        m.stdout = stdout
        m.stderr = stderr
        return m

    def test_install_calls_add_json_when_not_present(self, tmp_path: Path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{}", encoding="utf-8")

        with patch("subprocess.run") as mock_run:
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

        with patch("subprocess.run") as mock_run:
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

        with patch("shutil.which", return_value=None):
            with pytest.raises(claude_code.INSTALL_ERROR, match="claude.*not found"):
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
```

- [ ] Run RED: `uv run pytest tests/unit/test_agent_targets_claude_code.py -v` — expect all FAIL (`ModuleNotFoundError`).

- [ ] Commit RED:
  ```
  git commit -m "test(claude-code): add failing contract + subprocess-delegation tests"
  ```

---

## Step 2 — Create the adapter module (GREEN)

Create `src/modal_mcp/agent_targets/claude_code.py`. Key design points:

### Contract

```python
CONTRACT = AgentTargetContract(
    agent_name="claude_code",
    representative_config_path=Path("~/.claude.json"),  # actual MCP store, not settings.json
    config_format="json",
    mcp_transport="stdio",
    server_name="modal-mcp",
    top_level_key="mcpServers",
    server_url=None,
    server_command="modal-mcp",
    server_args_template=("stdio", "--env-file", "{env_file}"),
    env_file_strategy="absolute_path_flag",
    supports_cwd_config=False,
    backup_suffix_template="not_applicable_managed_by_claude_cli",
    refusal_conditions=(
        "claude CLI not found on PATH",
        "env_file is not an absolute path",
        "subprocess call to claude mcp add-json exits non-zero",
    ),
    parse_validation_strategy="run 'claude mcp get modal-mcp' and check exit code 0",
    dry_run_description=(
        "add mcpServers.modal-mcp entry via 'claude mcp add-json' (stdio transport, --scope user)"
    ),
    idempotency_key="mcpServers.modal-mcp",
)
```

### `render(env_file, scope="user", file=None)`

Validates `env_file` is absolute. Builds and prints (to `file` or stdout):

```text
# Run this command to register modal-mcp with Claude Code:
claude mcp add-json modal-mcp '{"type":"stdio","command":"modal-mcp","args":["stdio","--env-file","/abs/path/.env"]}' --scope user
```

### `install(env_file, dry_run, yes, scope="user", *, _claude_json_path=None)`

```text
1. Validate env_file is absolute. Raise ValueError if not.
2. Resolve _claude_json_path (for tests) or Path("~/.claude.json").expanduser().
3. If claude_json exists, read + parse it for idempotency check:
   - If mcpServers.modal-mcp exists AND matches expected entry (incl. type) → return "already_installed".
   - If mcpServers.modal-mcp exists AND differs → set needs_remove=True.
4. If dry_run:
   - Print target path, planned change, and the claude mcp add-json command.
   - Return "dry_run".
5. Check shutil.which("claude"). Raise ClaudeCodeInstallError if None (deferred so dry_run never requires it).
6. If not yes: prompt confirmation (like codex.py pattern). If declined → return "declined".
7. If needs_remove:
   - subprocess.run(["claude", "mcp", "remove", "modal-mcp", "--scope", scope], check=True)
8. Build JSON entry: {"type": "stdio", "command": "modal-mcp", "args": ["stdio", "--env-file", env_path_str]}
9. subprocess.run(["claude", "mcp", "add-json", "modal-mcp", json.dumps(entry), "--scope", scope], check=True)
   Wrap CalledProcessError → ClaudeCodeInstallError.
10. Return "installed".
```

- [ ] Implement `claude_code.py` following above spec.

- [ ] Run tests after each section:
  ```bash
  uv run pytest tests/unit/test_agent_targets_claude_code.py -v 2>&1 | tail -30
  ```

- [ ] Commit GREEN:
  ```
  git commit -m "feat(claude-code): add subprocess-delegation adapter for claude mcp add-json"
  ```

---

## Step 3 — Register in registry + update CLI choices

### `src/modal_mcp/agent_targets/__init__.py`

```python
from modal_mcp.agent_targets import claude, claude_code, codex

_TARGETS: Final[dict[str, ModuleType]] = {
    "codex": codex,
    "claude": claude,
    "claude_desktop": claude,
    "claude_code": claude_code,
    "claude-code": claude_code,  # hyphen matches --install token users type
}
```

### `src/modal_mcp/cli/setup.py`

Add `--scope` argument to `SetupCommand.register()` (after `--install`):

```python
parser.add_argument(
    "--scope",
    choices=["user", "project", "local"],
    default="user",
    help=(
        "MCP server scope for --install claude-code. "
        "user: all projects (default). "
        "project: current project via .mcp.json. "
        "local: current project, private (stored in ~/.claude.json). "
        "Ignored by other install targets."
    ),
)
```

### `src/modal_mcp/cli/print_agent_config.py`

Update `--target` choices to derive from registry (like `setup.py` already does) so
`claude_code` and `claude-code` appear automatically. If hard-coded, add them explicitly:

```python
choices=["claude", "claude_desktop", "codex", "claude_code", "claude-code"]
```

- [ ] Verify `--install` choices include new targets:
  ```bash
  uv run modal-mcp setup --help 2>&1 | grep -A2 install
  ```

- [ ] Verify `print-agent-config --target claude-code` works:
  ```bash
  uv run modal-mcp print-agent-config --target claude-code --env-file /tmp/test.env
  ```
  Expected: `claude mcp add-json modal-mcp '...' --scope user`

- [ ] Commit:
  ```
  git commit -m "feat(agent-targets): register claude_code + claude-code alias; add --scope flag"
  ```

---

## Step 4 — Create ADR

Create `docs/adr/0001-claude-code-delegates-to-cli.md`:

```markdown
# ADR 0001: Claude Code adapter delegates to `claude mcp add-json`

**Status:** Accepted  
**Date:** 2026-05-17

## Context

All other agent target adapters (codex, claude_desktop) write config files
directly (JSON merge / TOML merge). For Claude Code the natural target would
be `~/.claude.json`, but that file is a large opaque store managed by Claude
Code itself, containing per-project MCP entries, session state, and history.
Writing into it directly risks stomping unrelated data and breaks when
Claude Code changes its internal schema.

Claude Code exposes `claude mcp add-json <name> '<json>' --scope <scope>`
as the canonical write mechanism, handling backup and conflict resolution
internally.

## Decision

The `claude_code` adapter shells out to `claude mcp add-json` rather than
writing `~/.claude.json` directly. Idempotency is checked by reading
`~/.claude.json` for structural comparison before any subprocess call;
writing is delegated to the CLI.

## Consequences

- Install requires `claude` CLI on PATH; adapter errors with a clear message
  when absent.
- Backup behaviour is whatever Claude Code's CLI provides (not our `.bak.*`
  pattern).
- Future schema changes to `~/.claude.json` are Claude Code's responsibility.
- `print-agent-config --target claude-code` prints the `claude mcp add-json`
  command rather than a raw JSON snippet, matching what install actually runs.
```

- [ ] `mkdir -p docs/adr && <write file>`

- [ ] Commit:
  ```
  git commit -m "docs(adr): record claude-code adapter delegates to claude CLI"
  ```

---

## Step 5 — Fix docs/clients.md + README

### `docs/clients.md` Claude Code section changes

1. **Fix the file reference**: `~/.claude/settings.json` does NOT store `mcpServers`.
   MCP servers are stored in `~/.claude.json` (user/local scope) or `.mcp.json`
   (project scope).

2. **Add automated install opener**:
   ```
   Recommended: `modal-mcp setup --install claude-code` registers the server
   automatically via `claude mcp add-json`. The manual steps below are for
   operators who prefer direct control.
   ```

3. **Flip `run` → `stdio`** in all three JSON code blocks (canonical, absolute-exe
   fallback, `uv run` fallback). This is the correctness fix — `run` starts
   HTTP/Uvicorn; Claude Code expects stdio and times out.

4. **Replace the hand-edit instruction** with the `claude mcp add-json` command
   as the manual fallback (not hand-editing `settings.json` which is wrong):
   ```bash
   claude mcp add-json modal-mcp \
     '{"type":"stdio","command":"modal-mcp","args":["stdio","--env-file","/abs/path/.env"]}' \
     --scope user
   ```

5. **Remove** "Restart Claude Code after editing `settings.json`." — not needed
   with `claude mcp add-json`.

### `README.md`

Update Claude Code row in install table:

```md
| Claude Code install | `modal-mcp setup --install claude-code` |
```

- [ ] Edit `docs/clients.md`.

- [ ] Edit `README.md`.

- [ ] Commit:
  ```
  git commit -m "docs(clients): claude-code uses stdio transport + automated install via claude CLI"
  ```

---

## Step 6 — Final lint + full pytest + smoke tests

- [ ] `uv run ruff check . --fix && uv run ruff format src tests`

- [ ] `uv run pytest 2>&1 | tail -20` — expect all green, new `test_agent_targets_claude_code` suite included.

- [ ] Smoke: dry-run install (no subprocess, no file write):
  ```bash
  uv run modal-mcp setup --install claude-code --dry-run --env-file /tmp/example.env
  ```
  Expected: prints the planned `claude mcp add-json` command, no subprocess called.

- [ ] Smoke: print-agent-config:
  ```bash
  uv run modal-mcp print-agent-config --target claude-code --env-file /tmp/example.env
  ```
  Expected: `claude mcp add-json modal-mcp '{"type":"stdio",...}' --scope user`

- [ ] Commit if cleanup needed:
  ```
  git commit -m "chore(claude-code): lint and format cleanup"
  ```

---

## Self-review checklist

| Spec item | Step | Evidence |
|---|---|---|
| `--install claude-code` delegates to `claude mcp add-json` | 2, 6 | `install()` uses `subprocess.run`; smoke test |
| Stdio transport (not HTTP) | 2, 5 | `server_args_template[0] == "stdio"`; docs snippets fixed |
| Hyphen alias accepted | 3 | Registry `"claude-code"` key; `--install` choices |
| Idempotent: reads `~/.claude.json` before write | 2 | `test_install_is_idempotent_when_entry_matches` |
| Replaces when entry differs | 2 | `test_install_replaces_when_entry_differs` |
| Dry-run prints command, no subprocess | 2 | `test_install_dry_run_does_not_call_subprocess` |
| Errors when `claude` CLI missing | 2 | `test_install_errors_when_claude_cli_missing` |
| Absolute env-file enforced | 2 | `test_install_rejects_non_absolute_env_file` |
| `--scope` flag exposed | 3 | `setup.py` parser; passed through to adapter |
| `print-agent-config` prints `claude mcp add-json` | 3, 6 | smoke test |
| docs/clients.md: correct file + stdio args | 5 | clients.md edits |
| ADR captures delegation decision | 4 | `docs/adr/0001-*.md` |
| Full test suite green | 6 | `uv run pytest` |
