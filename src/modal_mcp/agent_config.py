"""Agent configuration print support for Modal MCP.

This module provides :func:`print_agent_config`, the single entry point used
by the ``modal-mcp print-agent-config --target <name>`` subcommand.  It reads
the agent target contract and emits a ready-to-paste configuration snippet to
a file-like object (default: ``sys.stdout``).

Design constraints
------------------

* **No external files are written.**  The function only writes to the supplied
  *file* argument (or ``sys.stdout``); it never touches the filesystem.
* **No secrets are leaked.**  The snippet contains no credentials, tokens, or
  sensitive environment values.  Only the structural config shape is printed.
* **Absolute env-file path.**  For stdio (command-launched) targets such as
  Codex, the snippet includes an absolute ``--env-file`` path so that the
  spawned server process can locate its settings regardless of the shell's
  working directory.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

__all__ = ["print_agent_config"]


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
    """Emit the Codex TOML config snippet.

    Transport: stdio — Codex launches ``modal-mcp`` as a subprocess.  The
    ``--env-file`` flag ensures the server finds its settings from any cwd.
    """
    from modal_mcp.agent_targets.codex import (
        CODEX_SERVER_COMMAND,
        format_config_snippet,
    )

    resolved_command = command if command is not None else CODEX_SERVER_COMMAND
    snippet = format_config_snippet(env_file=env_file, command=resolved_command)

    print("# Add this block to ~/.codex/config.toml", file=file)
    print("# Transport: stdio (Codex launches modal-mcp as a subprocess)", file=file)
    print(snippet, end="", file=file)


def _print_claude_config(
    *,
    env_file: str | Path | None,
    file: TextIO,
) -> None:
    """Emit the Claude Desktop JSON config snippet with startup command hint.

    Transport: HTTP/SSE — Claude Desktop connects to the running ``modal-mcp``
    server via Server-Sent Events.  The server must be started separately
    before Claude Desktop connects; use an absolute ``--env-file`` path so the
    server finds its settings regardless of the shell's working directory.
    """
    from modal_mcp.agent_targets.claude import (
        _ENV_FILE_PLACEHOLDER,
        format_config_snippet,
        format_startup_command,
    )

    # Build the startup command with an absolute env-file path.  If no path
    # was supplied, embed a descriptive placeholder so the snippet is still
    # syntactically usable and the user knows what to fill in.
    if env_file is not None:
        startup_tokens = format_startup_command(env_file)  # raises if relative
        startup_cmd = " ".join(startup_tokens)
    else:
        startup_cmd = f"modal-mcp run --env-file {_ENV_FILE_PLACEHOLDER}"

    print(
        "# Transport: HTTP/SSE (Claude Desktop connects to a running modal-mcp server)",
        file=file,
    )
    print(
        "# 1. Start the server with an absolute --env-file path:",
        file=file,
    )
    print(f"#    {startup_cmd}", file=file)
    print("# 2. Add this block to claude_desktop_config.json", file=file)
    print(format_config_snippet(), end="", file=file)
