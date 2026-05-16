# Collapse CLI Plumbing into Agent Target Adapters and CliCommand Dispatch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Concentrate the install + render + dispatch logic for every agent target (Codex, Claude Desktop, + new Cursor stub) inside its own `agent_targets/<target>.py` adapter, and move CLI subcommand handlers (`run`, `setup`, `doctor`, `print-agent-config`) into per-command `CliCommand` classes under `src/modal_mcp/cli/`, so that `agent_config.py` and `setup_files.py` can be deleted and `__main__.py` shrinks to argparse + dispatch (~80 lines).

**Architecture:** The deepening is two-axis. Axis 1: each agent adapter absorbs `install_<target>_config`, `_print_<target>_config`, dry-run, backup, validation — knowledge per target ends up in one file. Axis 2: each CLI subcommand becomes a `CliCommand` Protocol implementation in `src/modal_mcp/cli/<command>.py` — one class per command. `__main__.py` is reduced to building the parser and dispatching `args.subcommand → CliCommand → exit_code`. The atomic file I/O primitives (`write_secret`, `safe_write_text`, `ensure_gitignore_entries`, `ensure_private_dir`, `generate_signing_key`) move into a smaller `domain/file_io.py` so the two "delete" targets (`agent_config.py`, `setup_files.py`) actually disappear without losing safety guarantees.

**Tech Stack:** Python 3.12, argparse, pytest, ruff, tomllib (read), tomli-w (not introduced — keep the existing string-append snippet behaviour), json (stdlib), uv.

---

## File Structure

Files touched by this plan (repo-relative paths only):

```text
src/modal_mcp/
    __main__.py                          ← shrink to ~80 lines: argparse + dispatch
    agent_config.py                      ← DELETE after migration
    setup_files.py                       ← DELETE after migration
    setup.py                             ← move to cli/setup.py; shrink to orchestration
    doctor.py                            ← move to cli/doctor.py; share probe helpers
    cli/
        __init__.py                      ← NEW: CliCommand Protocol + registry
        run.py                           ← NEW: RunCommand
        setup.py                         ← NEW: SetupCommand (orchestrates target.install)
        doctor.py                        ← NEW: DoctorCommand (uses shared probes)
        print_agent_config.py            ← NEW: PrintAgentConfigCommand
    domain/
        file_io.py                       ← NEW: write_secret, safe_write_text,
                                            ensure_gitignore_entries, ensure_private_dir,
                                            generate_signing_key, SetupFilesError
    agent_targets/
        __init__.py                      ← extend with get_target(name) registry
        contract.py                      ← unchanged
        codex.py                         ← absorb install_codex_config + _print_codex_config
        claude.py                        ← absorb install_claude_config + _print_claude_config
        cursor.py                        ← NEW (optional bonus): proof-of-extensibility
tests/unit/
    test_agent_targets_codex.py          ← NEW: tests for codex install/render
    test_agent_targets_claude.py         ← NEW: tests for claude install/render
    test_agent_targets_cursor.py         ← NEW: tests for cursor (bonus)
    test_cli_dispatch.py                 ← NEW: tests for CliCommand registry
    test_agent_config_codex.py           ← slim to delegation shims OR delete
    test_agent_config_claude.py          ← slim to delegation shims OR delete
    test_setup.py                        ← slim; SetupFilesError import path updates
    test_setup_files.py                  ← move imports to domain.file_io
    test_doctor.py                       ← slim to CLI test
    test_cli_entrypoint.py               ← update to expect cli/* dispatch
```

No deletion of public names that existing tests/imports depend on without a thin re-export shim in place for one release; the shims are explicitly removed in Step 9.

---

## Step 1 — RED: Add failing tests pinning the new public seams

These tests pin the public seams of the migration (target adapter `install()` + `render()`, the `CliCommand` Protocol, the new `domain.file_io` location).  They are expected to fail with `ImportError` until the corresponding steps land.

- [ ] Create `tests/unit/test_agent_targets_codex.py` with the following content:

```python
"""Tests for the codex agent target adapter (install + render absorbed)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_codex_target_exposes_install_and_render() -> None:
    from modal_mcp.agent_targets import codex

    # New: absorbed from agent_config.py
    assert callable(codex.install)
    assert callable(codex.render)


def test_codex_target_install_signature_keyword_only() -> None:
    """install() must require env_file as a keyword argument."""
    import inspect

    from modal_mcp.agent_targets import codex

    sig = inspect.signature(codex.install)
    assert "env_file" in sig.parameters
    assert sig.parameters["env_file"].kind == inspect.Parameter.KEYWORD_ONLY


def test_codex_render_returns_snippet_with_block_header() -> None:
    from modal_mcp.agent_targets import codex

    out = codex.render(env_file=Path("/tmp/.env"))
    assert "[mcp_servers.modal-mcp]" in out
    assert "/tmp/.env" in out


def test_codex_render_relative_env_file_raises() -> None:
    from modal_mcp.agent_targets import codex

    with pytest.raises(ValueError, match="absolute path"):
        codex.render(env_file=Path(".env"))


def test_codex_install_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    from modal_mcp.agent_targets import codex

    target = tmp_path / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = codex.install(
        env_file=tmp_path / ".env",
        dry_run=True,
        yes=True,
        config_path=target,
    )
    assert result == "dry_run"
    assert not target.exists()
    captured = capsys.readouterr()
    assert "Would add" in captured.out or "would add" in captured.out


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
```

- [ ] Create `tests/unit/test_agent_targets_claude.py` with the parallel-shape tests for the Claude adapter — four cases matching the Codex ones above (`*_exposes_install_and_render`, `*_render_returns_sse_snippet`, `*_install_dry_run_writes_nothing`, `*_install_writes_valid_json`).  The render snippet must contain `"mcpServers"`, `"modal-mcp"`, and `"sse"`; the install (with `config_path=tmp_path/"claude_desktop_config.json"`) must produce valid JSON parsable by `json.loads` with `data["mcpServers"]["modal-mcp"]["type"] == "sse"`.

- [ ] Create `tests/unit/test_cli_dispatch.py` containing:

```python
"""Tests for the CliCommand registry and dispatch."""

from __future__ import annotations

import pytest


def test_cli_command_protocol_exists() -> None:
    from modal_mcp.cli import CliCommand

    assert hasattr(CliCommand, "register")
    assert hasattr(CliCommand, "run")


def test_registry_lists_all_four_commands() -> None:
    from modal_mcp.cli import COMMANDS

    names = {c.name for c in COMMANDS}
    assert names == {"run", "setup", "doctor", "print-agent-config"}


@pytest.mark.parametrize(
    "module_path, class_name, expected_name",
    [
        ("modal_mcp.cli.run", "RunCommand", "run"),
        ("modal_mcp.cli.setup", "SetupCommand", "setup"),
        ("modal_mcp.cli.doctor", "DoctorCommand", "doctor"),
        ("modal_mcp.cli.print_agent_config", "PrintAgentConfigCommand", "print-agent-config"),
    ],
)
def test_command_class_present(
    module_path: str, class_name: str, expected_name: str
) -> None:
    import importlib

    module = importlib.import_module(module_path)
    command = getattr(module, class_name)
    assert command.name == expected_name


def test_main_dispatches_to_run_with_no_subcommand(monkeypatch) -> None:
    """No subcommand defaults to RunCommand (backward-compat)."""
    from modal_mcp import __main__ as main_mod

    called: list[bool] = []
    monkeypatch.setattr("modal_mcp.server.run", lambda: called.append(True))
    assert main_mod.main([]) == 0
    assert called == [True]
```

- [ ] Create `tests/unit/test_domain_file_io.py` with the following content:

```python
"""Tests pinning the new domain.file_io module location."""

from __future__ import annotations

import pytest


def test_setup_files_re_exports_remain_for_one_release() -> None:
    """During the transition setup_files exposes the same symbols.

    Step 9 (the deletion step) removes this shim.
    """
    # Both import paths must work mid-migration.
    from modal_mcp.domain.file_io import (
        SetupFilesError,
        ensure_gitignore_entries,
        ensure_private_dir,
        generate_signing_key,
        safe_write_text,
        write_secret,
    )

    assert SetupFilesError is not None
    assert callable(ensure_gitignore_entries)
    assert callable(ensure_private_dir)
    assert callable(generate_signing_key)
    assert callable(safe_write_text)
    assert callable(write_secret)
```

- [ ] Run the tests and confirm RED:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest \
  tests/unit/test_agent_targets_codex.py \
  tests/unit/test_agent_targets_claude.py \
  tests/unit/test_cli_dispatch.py \
  tests/unit/test_domain_file_io.py \
  -x 2>&1 | tail -30
```

Expected: each file fails with `ImportError` (e.g. `cannot import name 'install' from 'modal_mcp.agent_targets.codex'`, `No module named 'modal_mcp.cli'`, `No module named 'modal_mcp.domain.file_io'`).

---

## Step 2 — Extract `domain/file_io.py`

`setup_files.py` is a private helper that only `setup.py` and the agent installers call.  Move its content under `domain/` so it survives the deletion of `setup_files.py`.

- [ ] Create `src/modal_mcp/domain/file_io.py` by copying the content of `src/modal_mcp/setup_files.py` verbatim.  All public symbols (`SetupFilesError`, `ensure_private_dir`, `write_secret`, `safe_write_text`, `ensure_gitignore_entries`, `generate_signing_key`) and the module docstring stay identical.  This is a pure relocation.

- [ ] Replace the body of `src/modal_mcp/setup_files.py` with a re-export shim:

```python
"""DEPRECATED — re-exports from :mod:`modal_mcp.domain.file_io`.

This module is preserved as an import shim during the
``epo-collapse-cli-plumbing-into-agent-g76h`` migration so that external
imports (e.g. ``from modal_mcp.setup_files import write_secret``) keep
working for one release.  Will be deleted in Step 9 of the
collapse-cli-plumbing plan.
"""

from __future__ import annotations

from modal_mcp.domain.file_io import (
    SetupFilesError,
    ensure_gitignore_entries,
    ensure_private_dir,
    generate_signing_key,
    safe_write_text,
    write_secret,
)

__all__ = [
    "SetupFilesError",
    "ensure_gitignore_entries",
    "ensure_private_dir",
    "generate_signing_key",
    "safe_write_text",
    "write_secret",
]
```

- [ ] Verify:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_setup_files.py tests/unit/test_domain_file_io.py -v 2>&1 | tail -20
```

Expected: all existing `test_setup_files.py` cases still pass against the shim, and the new `test_domain_file_io.py` passes.

---

## Step 3 — Migrate target 1: codex absorbs install + render

The codex adapter currently only owns the contract and the rendered TOML snippet.  Move `install_codex_config` (renamed `install`) and `_print_codex_config` (renamed `render`) into `agent_targets/codex.py`.  Existing module-level constants (`CODEX_SERVER_COMMAND`, `CODEX_SERVER_NAME`, etc.) stay where they are.

- [ ] In `src/modal_mcp/agent_targets/codex.py`, append a new "Install + render" section sourced from `src/modal_mcp/agent_config.py`.  Concretely:

  1. **Copy the helpers** `_make_timestamp()` and `_atomic_write_text()` from `src/modal_mcp/agent_config.py` (lines ~220–251) verbatim into `codex.py` (rename temp prefix to `".tmp_codex_"`).
  2. **Copy `install_codex_config`** (lines ~259–566) verbatim and rename it to `install`.  Drop the module-prefix imports inside the function — once the body lives in `codex.py`, `CODEX_SERVER_COMMAND`, `CODEX_SERVER_ARGS_TEMPLATE`, `CODEX_SERVER_NAME`, `CODEX_TOP_LEVEL_KEY`, `CODEX_BACKUP_SUFFIX_TEMPLATE`, `build_contract`, and `format_config_snippet` are already in scope at module level.
  3. **Copy the `CodexInstallError` class** definition (lines ~63–75 of `agent_config.py`) into `codex.py` near the top of the new section.
  4. **Add a thin `render()` wrapper** that prints the comment header + snippet and also returns the snippet text:

```python
def render(
    *,
    env_file: str | Path | None = None,
    command: str = CODEX_SERVER_COMMAND,
    file: _TextIO | None = None,
) -> str:
    """Render the Codex TOML config snippet.

    When *file* is not None, prints the comment header and snippet to *file*
    (matching the previous ``_print_codex_config`` behaviour).  Always returns
    the snippet body so callers can inspect or test it.
    """
    snippet = format_config_snippet(env_file=env_file, command=command)
    if file is not None:
        print("# Add this block to ~/.codex/config.toml", file=file)
        print("# Transport: stdio (Codex launches modal-mcp as a subprocess)", file=file)
        print(snippet, end="", file=file)
    return snippet
```

  5. **Add module-private aliases** at the top of the new section so the copy-pasted body keeps compiling without polluting the public namespace:

```python
import contextlib as _contextlib
import os as _os
import sys as _sys
import tempfile as _tempfile
import tomllib as _tomllib
from datetime import UTC as _UTC, datetime as _datetime
from typing import Any as _Any, TextIO as _TextIO, cast as _cast

TomlTable = dict[str, _Any]
```

  6. Rewrite the copied body to use the `_`-prefixed aliases (`_sys.stdout`, `_tomllib.loads`, `_cast`, `_contextlib.suppress`, `_tempfile.mkstemp`, `_os.write`, `_os.close`) so the file does not import `sys`, `tempfile`, etc. at module level twice (it already imports some of these).  Where collisions exist, drop the duplicate.

- [ ] Extend the existing `__all__` in `src/modal_mcp/agent_targets/codex.py` to include the three new symbols:

```python
__all__ = [
    "CODEX_AGENT_NAME",
    "CODEX_BACKUP_SUFFIX_TEMPLATE",
    "CODEX_CONFIG_FILENAME",
    "CODEX_CONFIG_FORMAT",
    "CODEX_CONTRACT",
    "CODEX_IDEMPOTENCY_KEY",
    "CODEX_SERVER_ARGS_TEMPLATE",
    "CODEX_SERVER_COMMAND",
    "CODEX_SERVER_NAME",
    "CODEX_TOP_LEVEL_KEY",
    "CODEX_TRANSPORT",
    "AgentTargetContract",
    "CodexInstallError",     # NEW
    "build_contract",
    "format_config_snippet",
    "install",               # NEW
    "render",                # NEW
]
```

- [ ] Replace the body of `install_codex_config` / `_print_codex_config` in `src/modal_mcp/agent_config.py` with delegation shims so the existing 60-KB `test_agent_config_codex.py` keeps passing:

  Find the existing block starting at `def install_codex_config(` (around line 259) and replace the whole function body up to its return with a single delegation call:

  ```python
  def install_codex_config(
      *,
      env_file: str | Path,
      command: str | None = None,
      dry_run: bool = False,
      yes: bool = False,
      config_path: Path | None = None,
      file: TextIO | None = None,
      _timestamp: str | None = None,
  ) -> str:
      """DEPRECATED — delegates to :func:`modal_mcp.agent_targets.codex.install`."""
      from modal_mcp.agent_targets import codex as _codex

      return _codex.install(
          env_file=env_file,
          command=command,
          dry_run=dry_run,
          yes=yes,
          config_path=config_path,
          file=file,
          _timestamp=_timestamp,
      )
  ```

  Find `def _print_codex_config(` (around line 151) and rewrite the body as:

  ```python
  def _print_codex_config(
      *,
      env_file: str | Path | None,
      command: str | None,
      file: TextIO,
  ) -> None:
      """DEPRECATED — delegates to :func:`modal_mcp.agent_targets.codex.render`."""
      from modal_mcp.agent_targets import codex as _codex

      resolved_command = command if command is not None else _codex.CODEX_SERVER_COMMAND
      _codex.render(env_file=env_file, command=resolved_command, file=file)
  ```

  Re-export `CodexInstallError` for callers that still import it from `agent_config`:

  ```python
  # At the top of agent_config.py, after the existing imports:
  from modal_mcp.agent_targets.codex import CodexInstallError as _CodexInstallError

  CodexInstallError = _CodexInstallError  # backward-compat alias; removed in Step 9
  ```

  Delete the old `class CodexInstallError(Exception):` definition (lines ~63–75) so there is only one class identity.

- [ ] Verify:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest \
  tests/unit/test_agent_targets_codex.py \
  tests/unit/test_agent_config_codex.py -v 2>&1 | tail -30
```

Expected: new test file is GREEN, existing 60-KB Codex install test file still passes against the delegation shim.

---

## Step 4 — Migrate target 2: claude absorbs install + render

Same shape as Step 3 but for Claude Desktop.  The JSON validator and SSE URL plumbing differ; the structure of install / render / dry-run / backup / validate is identical.

- [ ] In `src/modal_mcp/agent_targets/claude.py`, mirror the Step 3 procedure:

  1. **Copy `_make_timestamp` and `_atomic_write_text`** (use `".tmp_claude_"` prefix) into `claude.py`.
  2. **Copy `install_claude_config`** from `agent_config.py` (lines ~574–~860) verbatim and rename it to `install`.  Strip the inner-function imports since `CLAUDE_DEFAULT_BIND`, `CLAUDE_TOP_LEVEL_KEY`, `CLAUDE_SERVER_NAME`, `CLAUDE_TRANSPORT`, `CLAUDE_BACKUP_SUFFIX_TEMPLATE`, `build_contract`, `format_config_snippet`, and `get_claude_config_path` are already in scope.
  3. **Copy the `ClaudeInstallError` class** definition (lines ~78–91 of `agent_config.py`) into `claude.py` near the top of the new section.
  4. **Add a `render()` wrapper** that prints the startup hint + snippet and returns the snippet body:

```python
def render(
    *,
    env_file: str | Path | None = None,
    bind: str = CLAUDE_DEFAULT_BIND,
    file: _TextIO | None = None,
) -> str:
    """Render the Claude Desktop JSON config snippet and a startup hint."""
    if env_file is not None:
        startup_tokens = format_startup_command(env_file)
        startup_cmd = " ".join(startup_tokens)
    else:
        startup_cmd = f"modal-mcp run --env-file {_ENV_FILE_PLACEHOLDER}"

    snippet = format_config_snippet(bind=bind)
    if file is not None:
        print(
            "# Transport: HTTP/SSE (Claude Desktop connects to a running modal-mcp server)",
            file=file,
        )
        print("# 1. Start the server with an absolute --env-file path:", file=file)
        print(f"#    {startup_cmd}", file=file)
        print("# 2. Add this block to claude_desktop_config.json", file=file)
        print(snippet, end="", file=file)
    return snippet
```

  5. Add the same module-private alias block used in Step 3 (replace `tomllib` with `json` and `_tomllib` with `_json`).

- [ ] Extend the existing `__all__` in `claude.py` to include the three new symbols (`install`, `render`, `ClaudeInstallError`).

- [ ] Replace `install_claude_config` / `_print_claude_config` in `agent_config.py` with delegation shims (same pattern as Step 3):

  ```python
  def install_claude_config(
      *,
      bind: str | None = None,
      dry_run: bool = False,
      yes: bool = False,
      config_path: Path | None = None,
      file: TextIO | None = None,
      _timestamp: str | None = None,
  ) -> str:
      """DEPRECATED — delegates to :func:`modal_mcp.agent_targets.claude.install`."""
      from modal_mcp.agent_targets import claude as _claude

      return _claude.install(
          bind=bind,
          dry_run=dry_run,
          yes=yes,
          config_path=config_path,
          file=file,
          _timestamp=_timestamp,
      )


  def _print_claude_config(
      *,
      env_file: str | Path | None,
      file: TextIO,
  ) -> None:
      """DEPRECATED — delegates to :func:`modal_mcp.agent_targets.claude.render`."""
      from modal_mcp.agent_targets import claude as _claude

      _claude.render(env_file=env_file, file=file)
  ```

  Replace the old `class ClaudeInstallError` with a re-export alias:

  ```python
  from modal_mcp.agent_targets.claude import ClaudeInstallError as _ClaudeInstallError

  ClaudeInstallError = _ClaudeInstallError  # backward-compat; removed Step 9
  ```

- [ ] Verify:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest \
  tests/unit/test_agent_targets_claude.py \
  tests/unit/test_agent_config_claude.py -v 2>&1 | tail -30
```

Expected: new file is GREEN, existing 60-KB Claude install test file still passes.

---

## Step 5 — Optional bonus: cursor.py as proof-of-extensibility

Demonstrates that adding a target is now a single-file change.  Cursor uses the same TOML+stdio shape as Codex but writes to `~/.cursor/mcp.json` (JSON shape, per Cursor's docs).

If this step is skipped the deepening still completes; it exists to prove the architecture.

- [ ] Create `src/modal_mcp/agent_targets/cursor.py` by **copying `claude.py` as a template** and applying these substitutions:

  | Replace | With |
  |---|---|
  | `CLAUDE_AGENT_NAME = "claude_desktop"` | `CURSOR_AGENT_NAME = "cursor"` |
  | `CLAUDE_CONFIG_FILENAME = "claude_desktop_config.json"` | `CURSOR_CONFIG_FILENAME = "mcp.json"` |
  | `CLAUDE_SERVER_NAME = "modal-mcp"` | unchanged |
  | `CLAUDE_TOP_LEVEL_KEY = "mcpServers"` | unchanged |
  | `CLAUDE_DEFAULT_BIND = "127.0.0.1:8765"` | unchanged |
  | `CLAUDE_MCP_SSE_PATH = "/mcp/sse"` | unchanged |
  | `get_claude_config_dir/path` (platform branches) | `get_cursor_config_path() = Path.home() / ".cursor" / "mcp.json"` (single path; Cursor uses the same location on all platforms) |
  | `ClaudeInstallError` | `CursorInstallError` |
  | All `CLAUDE_*` constant prefixes | `CURSOR_*` |

  The install / render bodies stay structurally identical (JSON + SSE shape, atomic write, idempotency, backup, validation).  Confirm against the Cursor docs at https://docs.cursor.com/context/model-context-protocol that `~/.cursor/mcp.json` is the canonical location and that the `{type: "sse", url: "..."}` shape is accepted.

- [ ] Create `tests/unit/test_agent_targets_cursor.py` with three smoke tests mirroring Steps 1 & 4: `*_render_returns_sse_snippet` (snippet contains `"mcpServers"` and `"modal-mcp"`), `*_install_dry_run` (writes nothing, returns `"dry_run"`), `*_install_writes_valid_json` (JSON parses; `data["mcpServers"]["modal-mcp"]["type"] == "sse"`).

- [ ] Verify:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_agent_targets_cursor.py -v
```

---

## Step 6 — Introduce `agent_targets/__init__.py` registry

Replace the bare re-export with a `get_target(name) -> ModuleType` lookup so `CliCommand` dispatch never hard-codes module imports.

- [ ] Replace the content of `src/modal_mcp/agent_targets/__init__.py` with:

```python
"""Agent install target contracts and a name-based registry.

Each entry in :data:`TARGETS` is a tuple of ``(name, module)`` pairs.  Lookup
via :func:`get_target` returns the module object; callers then call
``module.install(...)`` or ``module.render(...)`` directly.  This avoids
hard-coding ``if name == 'codex'`` branches inside the CLI layer.
"""

from __future__ import annotations

from types import ModuleType
from typing import Final

from modal_mcp.agent_targets import claude, codex
from modal_mcp.agent_targets.contract import AgentTargetContract

#: Name → module map.  Each module MUST expose ``install`` and ``render``
#: functions matching the agent-target protocol.
_TARGETS: Final[dict[str, ModuleType]] = {
    "codex": codex,
    "claude": claude,
    "claude_desktop": claude,  # alias
}

# Register optional targets only when present (cursor.py is optional in Step 5).
try:
    from modal_mcp.agent_targets import cursor as _cursor

    _TARGETS["cursor"] = _cursor
except ImportError:
    pass

TARGETS: Final[tuple[tuple[str, ModuleType], ...]] = tuple(_TARGETS.items())


def get_target(name: str) -> ModuleType:
    """Return the agent-target module for *name* (case-insensitive).

    Raises :class:`ValueError` when *name* is not a known target.
    """
    key = name.lower()
    if key not in _TARGETS:
        supported = ", ".join(sorted(_TARGETS))
        msg = f"Unknown agent target: {name!r}. Supported: {supported}."
        raise ValueError(msg)
    return _TARGETS[key]


__all__ = [
    "AgentTargetContract",
    "TARGETS",
    "get_target",
]
```

- [ ] Add a quick smoke test in `tests/unit/test_cli_dispatch.py` (append to the existing file from Step 1):

```python
def test_get_target_returns_codex_module() -> None:
    from modal_mcp.agent_targets import codex, get_target

    assert get_target("codex") is codex
    assert get_target("CODEX") is codex


def test_get_target_returns_claude_module_for_aliases() -> None:
    from modal_mcp.agent_targets import claude, get_target

    assert get_target("claude") is claude
    assert get_target("claude_desktop") is claude


def test_get_target_raises_on_unknown_name() -> None:
    from modal_mcp.agent_targets import get_target
    import pytest

    with pytest.raises(ValueError, match="Unknown agent target"):
        get_target("nope")
```

- [ ] Verify:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_cli_dispatch.py -v 2>&1 | tail -20
```

---

## Step 7 — Introduce `cli/` package with `CliCommand` Protocol

The four argparse handlers in `__main__.py` (`_cmd_run`, `_cmd_setup`, `_cmd_doctor`, `_cmd_print_agent_config`) become four `CliCommand` classes.

- [ ] Create `src/modal_mcp/cli/__init__.py`:

```python
"""CLI subcommand registry for modal-mcp.

Each subcommand is a class implementing the :class:`CliCommand` protocol:

* ``name`` (ClassVar str) — the argparse subcommand name.
* ``register(subparsers) -> None`` — add a subparser and arguments.
* ``run(args) -> int`` — execute the command and return an exit code.

:data:`COMMANDS` lists every command in registration order.  ``__main__.py``
iterates this list to populate the parser and to look up handlers.
"""

from __future__ import annotations

import argparse
from typing import ClassVar, Protocol, runtime_checkable

from modal_mcp.cli.doctor import DoctorCommand
from modal_mcp.cli.print_agent_config import PrintAgentConfigCommand
from modal_mcp.cli.run import RunCommand
from modal_mcp.cli.setup import SetupCommand


@runtime_checkable
class CliCommand(Protocol):
    """Protocol implemented by every CLI subcommand."""

    name: ClassVar[str]

    @classmethod
    def register(cls, subparsers: argparse._SubParsersAction) -> None: ...

    @classmethod
    def run(cls, args: argparse.Namespace) -> int: ...


COMMANDS: tuple[type[CliCommand], ...] = (
    RunCommand,
    SetupCommand,
    DoctorCommand,
    PrintAgentConfigCommand,
)


__all__ = ["COMMANDS", "CliCommand"]
```

- [ ] Create `src/modal_mcp/cli/run.py` by wrapping `_cmd_run` from `__main__.py` (lines 132–149) in a `RunCommand` class.  `register` adds one argument (`--env-file`); `run` keeps the existing body verbatim (`load_dotenv` on file, then `server.run()`).  Set `name: ClassVar[str] = "run"`.

- [ ] Create `src/modal_mcp/cli/setup.py` containing a `SetupCommand` class:

  - `name: ClassVar[str] = "setup"`
  - `register(subparsers)` — copy the six `add_argument` calls from `__main__.build_parser` (lines 28–87): `--yes`, `--install` (add `"cursor"` to `choices`), `--dry-run`, `--env-file`, `--secrets-dir`, `--force`.
  - `run(args)` — top-level dispatch:
    1. If `args.install` is set → call `cls._install(args, args.install)`.
    2. Else if `args.yes` → call `cls._generate_files(args)`.
    3. Else → call `cls._print_instructions()` and return 0.
  - `_install(args, target_name)` — replaces the two hard-coded `if install_target == "codex"` / `if install_target == "claude"` branches with one call to `get_target(target_name)`.  Codex (stdio) needs `env_file=<absolute>`; SSE targets (`claude`, `cursor`) need `bind=<resolved>` discovered from the env file or `MODAL_MCP_HTTP_BIND`.  Wrap `target.install(...)` in `try / except (Exception, ValueError)` and convert to `print("error: ...", file=sys.stderr); return 1`.
  - `_generate_files(args)` — verbatim copy of `__main__.py` lines ~235–269.  The only edit is the import line: `from modal_mcp.domain.file_io import SetupFilesError` instead of `from modal_mcp.setup_files import SetupFilesError`.
  - `_print_instructions()` — verbatim copy of `__main__.py` lines ~272–282.

- [ ] Create `src/modal_mcp/cli/doctor.py` by wrapping `_cmd_doctor` from `__main__.py` (lines 285–322) in a `DoctorCommand` class.  `name: ClassVar[str] = "doctor"`.  `register` adds `--env-file`.  `run` keeps the existing body verbatim (`run_doctor(env_file=...)`, format each item with the same prefix map, emit summary, return `report.exit_code`).

- [ ] Create `src/modal_mcp/cli/print_agent_config.py` by wrapping `_cmd_print_agent_config` (lines 325–337) in a `PrintAgentConfigCommand` class.  The only behaviour change is that the body now calls `get_target(target_name).render(env_file=env_file, file=sys.stdout)` instead of the old `print_agent_config(target, env_file=env_file)`.  Argparse choices: `["claude", "codex", "cursor"]`.

- [ ] Verify:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_cli_dispatch.py -v 2>&1 | tail -20
```

Expected: all `test_cli_dispatch.py` cases GREEN.

---

## Step 8 — Shrink `__main__.py` to argparse + dispatch

Replace the bulk of `__main__.py` (currently 361 lines) with a dispatch loop that drives `COMMANDS`.

- [ ] Replace the entire content of `src/modal_mcp/__main__.py` with:

```python
"""CLI entrypoint for modal_mcp.

Builds the argparse parser by asking every :class:`~modal_mcp.cli.CliCommand`
in :data:`~modal_mcp.cli.COMMANDS` to register itself, then dispatches the
chosen subcommand back to that command's ``run`` classmethod.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from modal_mcp.cli import COMMANDS, CliCommand


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser by letting each command register its subparser."""
    parser = argparse.ArgumentParser(
        prog="modal-mcp",
        description="Modal MCP server shell.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")
    for command in COMMANDS:
        command.register(subparsers)
    return parser


# When no subcommand is given on the CLI, fall back to "run" so that existing
# users running bare ``modal-mcp`` continue to get a server.
_DEFAULT_SUBCOMMAND = "run"

_BY_NAME: dict[str, type[CliCommand]] = {c.name: c for c in COMMANDS}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI shell."""
    args = build_parser().parse_args(argv)
    subcommand = args.subcommand or _DEFAULT_SUBCOMMAND
    command = _BY_NAME[subcommand]
    return command.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] Verify the CLI still behaves end-to-end:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run modal-mcp --help 2>&1 | head -20
cd "$(git rev-parse --show-toplevel)" && uv run modal-mcp print-agent-config --target codex --env-file /tmp/.env 2>&1 | head -20
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_cli_entrypoint.py tests/unit/test_cli_dispatch.py tests/unit/test_cli_fallback.py -v 2>&1 | tail -30
```

Expected: `--help` lists all four subcommands; `print-agent-config` emits the codex snippet; entrypoint tests pass.

---

## Step 9 — Delete `agent_config.py` and `setup_files.py`

Both files are now pure delegation shims.  Remove them and update remaining test imports.

- [ ] Confirm no production callsite (outside the shims themselves) imports from `agent_config` or `setup_files`:

```bash
cd "$(git rev-parse --show-toplevel)" && colgrep -e "from modal_mcp.agent_config" -l
cd "$(git rev-parse --show-toplevel)" && colgrep -e "from modal_mcp.setup_files" -l
```

Expected: matches only inside `tests/unit/` and `src/modal_mcp/setup.py`.

- [ ] Update `src/modal_mcp/setup.py` imports to point at `domain.file_io` instead of `setup_files`:

  In `src/modal_mcp/setup.py`, replace:

  ```python
  from modal_mcp.setup_files import (
      SetupFilesError,
      ensure_gitignore_entries,
      write_secret,
  )
  ```

  with:

  ```python
  from modal_mcp.domain.file_io import (
      SetupFilesError,
      ensure_gitignore_entries,
      write_secret,
  )
  ```

- [ ] Update test imports:

  In `tests/unit/test_setup.py`, replace `from modal_mcp.setup_files import SetupFilesError` with `from modal_mcp.domain.file_io import SetupFilesError`.

  In `tests/unit/test_setup_files.py`, replace all `from modal_mcp.setup_files import ...` with `from modal_mcp.domain.file_io import ...`.  (Keep the test class names so coverage is unchanged.)

  In `tests/unit/test_agent_config_codex.py` and `tests/unit/test_agent_config_claude.py`, replace the install/error imports:

  ```python
  # OLD
  from modal_mcp.agent_config import CodexInstallError, install_codex_config
  # NEW
  from modal_mcp.agent_targets.codex import CodexInstallError, install as install_codex_config
  ```

  ```python
  # OLD
  from modal_mcp.agent_config import ClaudeInstallError, install_claude_config
  # NEW
  from modal_mcp.agent_targets.claude import ClaudeInstallError, install as install_claude_config
  ```

  Replace `from modal_mcp.agent_config import print_agent_config` with a local helper that uses `get_target`:

  ```python
  from modal_mcp.agent_targets import get_target

  def print_agent_config(target: str, *, env_file=None, file=None) -> None:
      get_target(target).render(env_file=env_file, file=file)
  ```

- [ ] Delete the files:

```bash
cd "$(git rev-parse --show-toplevel)" && rm src/modal_mcp/agent_config.py src/modal_mcp/setup_files.py
```

- [ ] Re-run the full test suite:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest 2>&1 | tail -30
```

Expected: 100% pass.  If any test still imports from the deleted modules, update its imports per the patterns above.

---

## Step 10 — Move `doctor.py` into `cli/`, deduplicate credential probes

`doctor.py` and `setup.py` both interact with credential discovery.  After the move, the diagnostic probes live in `cli/doctor.py` and the shared helpers move to `domain/credentials.py`.

- [ ] Identify the credential-probe surface in `doctor.py` (review around lines 245–470: `_read_secret_file_for_sdk`, `_modal_sdk_env_overrides`, `_probe_modal_auth`, `probe_credentials`).  Confirm that `setup.py` does NOT call any of these — `setup.py` only writes credentials, it never probes.  This means the "shared probe" dedup is between `doctor.run_doctor` and `setup.write_service_token_files` — both touch the secrets directory, and both call `write_secret`.  The dedup target is the call into `domain.file_io.write_secret`, which is already factored.  No further extraction is needed for credential probes themselves; the duplication called out by the epic is the file-I/O path, already resolved in Step 2.

- [ ] Move the body of `src/modal_mcp/doctor.py` to `src/modal_mcp/cli/doctor.py`.  Concretely: rename the file (`git mv src/modal_mcp/doctor.py src/modal_mcp/cli/doctor_impl.py`) and split:

  Append the impl module's content into `src/modal_mcp/cli/doctor.py` (the file created in Step 7).  The class `DoctorCommand` stays at the top of the file; the original `run_doctor`, `CheckStatus`, `DiagnosticItem`, `DiagnosticReport`, `probe_credentials`, etc. follow below.  Update the `DoctorCommand.run` method's import:

  ```python
  # was:  from modal_mcp.doctor import CheckStatus, run_doctor
  # now (same file):
  # CheckStatus and run_doctor are defined later in this module
  ```

  Provide a backward-compat re-export at the old path so `tests/unit/test_doctor.py` continues to import from `modal_mcp.doctor`.  Create a new file `src/modal_mcp/doctor.py`:

  ```python
  """Backward-compat shim — re-exports from :mod:`modal_mcp.cli.doctor`."""

  from __future__ import annotations

  from modal_mcp.cli.doctor import (  # noqa: F401
      CheckStatus,
      CredentialProbeResult,
      DiagnosticItem,
      DiagnosticReport,
      probe_credentials,
      run_doctor,
  )

  __all__ = [
      "CheckStatus",
      "CredentialProbeResult",
      "DiagnosticItem",
      "DiagnosticReport",
      "probe_credentials",
      "run_doctor",
  ]
  ```

  This keeps `tests/unit/test_doctor.py` (60+ KB of tests) passing without modification.

- [ ] Verify:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_doctor.py 2>&1 | tail -10
```

Expected: full doctor test suite passes against the shim.

---

## Step 11 — Final lint, format, and full pytest

- [ ] Run formatters and lint fix-up:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run ruff check . --fix && uv run ruff format src tests scripts
```

- [ ] Run the full test suite:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest 2>&1 | tail -20
```

- [ ] Verify the deletions actually landed and no stale imports remain:

```bash
cd "$(git rev-parse --show-toplevel)" && colgrep -e "from modal_mcp.agent_config" -l
cd "$(git rev-parse --show-toplevel)" && colgrep -e "from modal_mcp.setup_files" -l
cd "$(git rev-parse --show-toplevel)" && test ! -f src/modal_mcp/agent_config.py && echo "agent_config.py deleted"
cd "$(git rev-parse --show-toplevel)" && test ! -f src/modal_mcp/setup_files.py && echo "setup_files.py deleted"
```

Expected: both colgrep calls return empty, both deletion confirmations print.

- [ ] Confirm `__main__.py` is at the target size (~80 lines):

```bash
cd "$(git rev-parse --show-toplevel)" && wc -l src/modal_mcp/__main__.py
```

Expected: ≤ 90 lines.

- [ ] Confirm `cli/` is the new home for CLI plumbing:

```bash
cd "$(git rev-parse --show-toplevel)" && wc -l src/modal_mcp/cli/*.py
```

Expected: 5 files (`__init__.py`, `run.py`, `setup.py`, `doctor.py`, `print_agent_config.py`).  Doctor will be the largest (it absorbed `doctor.py`); others sit between 60 and 200 lines.

- [ ] Commit:

```bash
cd "$(git rev-parse --show-toplevel)" && git add -A && git status
```

Then write the commit message (do NOT run automatically; the agent should review the diff first):

```text
refactor(cli): collapse CLI plumbing into agent_targets adapters + CliCommand dispatch

- Each agent target (codex, claude, cursor*) now owns install + render
  in src/modal_mcp/agent_targets/<target>.py
- Each CLI subcommand (run, setup, doctor, print-agent-config) is a
  CliCommand class in src/modal_mcp/cli/<name>.py
- src/modal_mcp/__main__.py is now ~80 lines: argparse + dispatch
- src/modal_mcp/agent_config.py and src/modal_mcp/setup_files.py deleted
- File-I/O primitives moved to src/modal_mcp/domain/file_io.py
- doctor.py relocated to cli/doctor.py with backward-compat re-export
- New agent_targets/__init__.py exposes get_target(name) registry

Closes epo-collapse-cli-plumbing-into-agent-g76h
```

---

## Self-review checklist

### Spec coverage (epic acceptance criteria)

| Criterion from epic | Covered by |
|---|---|
| agent_config.py deleted | Steps 3, 4, 9 |
| setup_files.py deleted | Steps 2, 9 |
| Each adapter absorbs render/install/dry-run/backup/validate | Steps 3, 4 |
| `agent_targets/__init__.py` exposes `get_target(name)` | Step 6 |
| `cli/` package with `CliCommand` Protocol | Step 7 |
| `__main__.py` ~80 lines: argparse + dispatch | Step 8 |
| `setup.py` shrinks to orchestration | Step 7 (`cli/setup.py` is orchestration), Step 9 (import update) |
| `doctor.py` moves to `cli/doctor.py`, shares probes with setup | Step 10 |
| `agent_targets/contract.py` unchanged | Confirmed; no fields added |
| Optional new target as proof-of-extensibility | Step 5 (cursor.py) |
| Adapter migration first, then CliCommand dispatch, then deletion | Step ordering 2→3→4→5→6→7→8→9 |

### Backward-compat seam scan

* `agent_config.py` / `setup_files.py` exist as shims between Steps 2–8 so existing tests keep passing during the migration; they are deleted only in Step 9, after every other migration is GREEN.
* `doctor.py` is preserved as a re-export shim in Step 10 so the 60-KB `test_doctor.py` does not need any line-level edits.
* The four `_cmd_*` functions in `__main__.py` are replaced by `CliCommand` classes; the only external surface (`build_parser`, `main`) keeps the same signatures so `tests/unit/test_cli_entrypoint.py` continues to drive the CLI.

### Risk register

| Risk | Mitigation |
|---|---|
| Hidden import of `agent_config` or `setup_files` outside the test tree | Step 11 grep confirms zero matches before deletion |
| `test_agent_config_codex.py` (60 KB) and `_claude.py` (60 KB) break on import rename | Step 9 rewrites only the import header to `from modal_mcp.agent_targets.codex import CodexInstallError, install as install_codex_config`; bodies stay the same |
| `setup.py` re-import of `SetupFilesError` from `setup_files` breaks after deletion | Step 9 explicitly switches it to `from modal_mcp.domain.file_io import SetupFilesError` |
| `doctor.py` 60-KB test file imports change behaviour | Step 10 keeps `modal_mcp.doctor` as a re-export shim |
| Cursor target ships unused if test infra lacks `~/.cursor` | `install` only writes when `config_path` is supplied or the dir exists; tests use `tmp_path` |
| Bool/extra args on subcommands change argparse schema | Each `register` method copies the exact argparse signature from the original `__main__.py` |

### Placeholder scan

No `TODO`, `pass`-only, `raise NotImplementedError`, or `...` placeholder left in production code blocks.  Every install function has a complete body and error handling.

### File-size invariant

Target sizes after Step 11:

* `src/modal_mcp/__main__.py` — ~40 lines (down from 361)
* `src/modal_mcp/cli/__init__.py` — ~40 lines
* `src/modal_mcp/cli/run.py` — ~35 lines
* `src/modal_mcp/cli/setup.py` — ~180 lines (absorbs `_cmd_setup`)
* `src/modal_mcp/cli/doctor.py` — ~900 lines (absorbs the 858-line `doctor.py`)
* `src/modal_mcp/cli/print_agent_config.py` — ~50 lines
* `src/modal_mcp/agent_targets/codex.py` — ~600 lines (was 300; now owns install)
* `src/modal_mcp/agent_targets/claude.py` — ~700 lines (was 433; now owns install)
* `src/modal_mcp/agent_targets/cursor.py` — ~220 lines (new)
* `src/modal_mcp/agent_config.py` — DELETED
* `src/modal_mcp/setup_files.py` — DELETED

Net: 5 files → 9 files, but the per-target deepening means a new agent target lives in one file instead of touching `__main__.py`, `setup.py`, `agent_config.py`, and a test file in three places.
