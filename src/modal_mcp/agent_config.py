"""Agent configuration print and install support for Modal MCP.

This module provides three main entry points:

- :func:`print_agent_config` — prints a ready-to-paste config snippet to
  a file-like object (default: ``sys.stdout``).  It never touches the
  filesystem and never leaks secrets.

- :func:`install_codex_config` — installs the ``[mcp_servers.modal-mcp]``
  entry into ``~/.codex/config.toml`` with confirmation, atomic backup,
  idempotency, and post-write validation.

- :func:`install_claude_config` — installs the ``mcpServers.modal-mcp``
  entry into ``claude_desktop_config.json`` with confirmation, atomic backup,
  idempotency, and post-write validation.

Design constraints
------------------

* **No secrets are leaked.**  Neither function reads credentials, tokens, or
  sensitive environment values.  Only the structural config shape is
  printed or written.
* **Absolute env-file path.**  For stdio (command-launched) targets such as
  Codex, both the printed snippet and the installed config use an absolute
  ``--env-file`` path so that the spawned server process can locate its
  settings regardless of the shell's working directory.
* **Atomic writes.**  Config files are written via a sibling temp file that
  is renamed into place, so readers never observe a partial write.
* **Backup before write.**  When an existing config file is present it is
  copied to a timestamped backup before the new entry is merged.
* **Idempotent.**  Re-running install when the entry is already present and
  correct is a no-op that returns ``"already_installed"`` without prompting.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

# Backward-compat re-exports during the collapse-cli-plumbing migration.
# CodexInstallError and ClaudeInstallError now live in
# modal_mcp.agent_targets.{codex,claude}.
from modal_mcp.agent_targets.claude import ClaudeInstallError as ClaudeInstallError
from modal_mcp.agent_targets.codex import CodexInstallError as CodexInstallError

__all__ = [
    "ClaudeInstallError",
    "CodexInstallError",
    "install_claude_config",
    "install_codex_config",
    "print_agent_config",
]


# ---------------------------------------------------------------------------
# Print support
# ---------------------------------------------------------------------------


def print_agent_config(
    target: str,
    *,
    env_file: str | Path | None = None,
    command: str | None = None,
    file: TextIO | None = None,
) -> None:
    """Print a complete agent configuration snippet for *target*.

    The output is a ready-to-paste config block appropriate for *target*.  For
    stdio (command-launched) targets the block uses an absolute
    ``--env-file`` path so that the spawned server finds its settings
    regardless of the working directory.

    This function **never writes external files** and **never leaks secrets**.

    Args:
        target: The agent target name.  Supported values: ``"codex"``,
            ``"claude"`` / ``"claude_desktop"``.  Case-insensitive.
        env_file: Absolute path to the ``.env`` file.  Required only for
            stdio targets (e.g. ``"codex"``).  If ``None``, a descriptive
            placeholder is used so the snippet is syntactically valid and
            the user can fill in the real path.  Passing a relative path
            raises :exc:`ValueError`.
        command: Override the ``modal-mcp`` executable embedded in the
            snippet.  Defaults to the target's standard command constant.
            Useful when ``modal-mcp`` is installed at a non-default absolute
            path.
        file: File-like object to write to.  Defaults to ``sys.stdout``.

    Raises:
        ValueError: If *target* is not a recognised target name, or if
            *env_file* is a relative path.
    """
    out = sys.stdout if file is None else file

    target_key = target.lower()
    if target_key == "codex":
        _print_codex_config(env_file=env_file, command=command, file=out)
    elif target_key in {"claude", "claude_desktop"}:
        _print_claude_config(env_file=env_file, file=out)
    else:
        supported = "'codex', 'claude'"
        msg = f"Unknown target: {target!r}.  Supported targets: {supported}."
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Target-specific renderers
# ---------------------------------------------------------------------------


def _print_codex_config(
    *,
    env_file: str | Path | None,
    command: str | None,
    file: TextIO,
) -> None:
    """DEPRECATED — delegates to :func:`modal_mcp.agent_targets.codex.render`."""
    from modal_mcp.agent_targets import codex as _codex

    _codex.render(env_file=env_file, command=command, file=file)


def _print_claude_config(
    *,
    env_file: str | Path | None,
    file: TextIO,
) -> None:
    """DEPRECATED — delegates to :func:`modal_mcp.agent_targets.claude.render`."""
    from modal_mcp.agent_targets import claude as _claude

    _claude.render(env_file=env_file, file=file)


# ---------------------------------------------------------------------------
# Codex install — public API (delegation shim)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Claude Desktop install — public API (delegation shim)
# ---------------------------------------------------------------------------


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
