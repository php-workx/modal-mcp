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

import contextlib
import json as _json
import os
import sys
import tempfile
import tomllib  # noqa: F401 — kept for backward-compat test monkeypatch path
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, cast

# Backward-compat re-exports during the collapse-cli-plumbing migration.
# CodexInstallError now lives in modal_mcp.agent_targets.codex.
from modal_mcp.agent_targets.codex import CodexInstallError as CodexInstallError

__all__ = [
    "ClaudeInstallError",
    "CodexInstallError",
    "install_claude_config",
    "install_codex_config",
    "print_agent_config",
]

TomlTable = dict[str, Any]


class ClaudeInstallError(Exception):
    """Raised when a Claude Desktop config install fails a safety check.

    Possible causes include (but are not limited to):
    - Unsupported platform: cannot determine the config path.
    - Config directory does not exist (Claude Desktop not installed).
    - Config file is a symlink (refused to follow).
    - Config file is not a regular file (directory, device, FIFO …).
    - Config file cannot be parsed as valid JSON.
    - Top-level config value is not a JSON object.
    - ``mcpServers`` value is present but is not a JSON object.
    - ``mcpServers.modal-mcp`` already exists with an incompatible entry.
    - Post-write validation failed (backup restored automatically).
    """


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


# ---------------------------------------------------------------------------
# Install helpers (private)
# ---------------------------------------------------------------------------


def _make_timestamp() -> str:
    """Return a compact UTC datetime string suitable for backup filenames.

    Format: ``YYYYMMDDTHHmmss`` (e.g. ``'20260419T103000'``).
    The compact format avoids ``:`` characters, which are illegal in
    filenames on Windows NTFS/FAT32 filesystems.
    """
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically write text *content* to *path* via a sibling temp file.

    The temp file is written in the same directory as *path* so that the
    final ``os.replace`` is a same-filesystem rename (atomic on POSIX).
    The temp file is cleaned up if an error occurs before the rename.
    """
    encoded = content.encode()
    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_codex_")
        tmp_path = Path(tmp_str)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)
        tmp_path.replace(path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()


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
# Claude Desktop install — public API
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
    """Install the ``mcpServers.modal-mcp`` entry into Claude Desktop config.

    The function supports dry-run previews, interactive confirmation, atomic
    backup, idempotent re-runs, and post-write validation.  Claude Desktop uses
    SSE transport — the server must be started separately with an absolute
    ``--env-file`` path before Claude connects; no env-file argument is
    embedded in the config entry itself.

    Args:
        bind: Optional ``host:port`` override for the SSE server URL embedded
            in the config entry.  Defaults to ``127.0.0.1:8765`` (the value
            from :data:`~modal_mcp.agent_targets.claude.CLAUDE_DEFAULT_BIND`).
            Pass ``settings.bind`` when the user has configured a non-default
            address so that the registered URL matches the running server.
        dry_run: When ``True``, print the target file and the exact change
            that *would* be made, then return ``"dry_run"`` without touching
            the filesystem.
        yes: When ``True``, skip the interactive confirmation prompt.
        config_path: Override the default install target
            (platform-specific ``claude_desktop_config.json``).  Useful in
            tests or when the user's config is in a non-standard location.
        file: Output stream for status messages.  Defaults to ``sys.stdout``.
        _timestamp: Override the timestamp embedded in the backup suffix.
            Intended for deterministic tests only.

    Returns:
        One of the following strings:

        ``"installed"``
            The entry was successfully written to the config file.
        ``"already_installed"``
            The entry already existed with the correct type and URL — no
            change was made.
        ``"declined"``
            The user declined the confirmation prompt.
        ``"dry_run"``
            Dry-run mode was active; no changes were made.

    Raises:
        ClaudeInstallError: If a safety check fails (e.g. unsupported platform,
            config directory missing, config file is a symlink, existing JSON
            is unparseable, conflicting entry exists, or post-write validation
            fails).
    """
    from modal_mcp.agent_targets.claude import (
        CLAUDE_BACKUP_SUFFIX_TEMPLATE,
        CLAUDE_SERVER_NAME,
        CLAUDE_TOP_LEVEL_KEY,
        CLAUDE_TRANSPORT,
        build_contract,
        get_claude_config_path,
    )

    out = sys.stdout if file is None else file

    # ------------------------------------------------------------------
    # 1.  Build contract and resolve arguments
    # ------------------------------------------------------------------

    contract = build_contract(bind) if bind is not None else build_contract()
    sse_url = contract.server_url  # always a non-None str for SSE transport

    # The entry that will be merged into the config file.
    new_entry: TomlTable = {"type": CLAUDE_TRANSPORT, "url": sse_url}
    # JSON snippet for display (dry-run, confirmation prompt).
    snippet = (
        _json.dumps({CLAUDE_TOP_LEVEL_KEY: {CLAUDE_SERVER_NAME: new_entry}}, indent=2)
        + "\n"
    )

    # Config path: use the runtime platform-specific path by default.
    if config_path is None:
        runtime_path = get_claude_config_path()
        if runtime_path is None:
            raise ClaudeInstallError(
                "Unsupported platform: cannot determine the Claude Desktop"
                " config path. Pass config_path explicitly to override."
            )
        resolved_config = runtime_path.expanduser().absolute()
    else:
        resolved_config = Path(config_path).absolute()

    # ------------------------------------------------------------------
    # 2.  Dry-run: show the planned change and exit early.
    # ------------------------------------------------------------------

    if dry_run:
        print(f"Target: {resolved_config}", file=out)
        print(f"Change: {contract.dry_run_description}", file=out)
        print(file=out)
        print("Would add to config:", file=out)
        print(snippet, end="", file=out)
        return "dry_run"

    # ------------------------------------------------------------------
    # 3.  Safety checks
    # ------------------------------------------------------------------

    # Config directory must exist (implies Claude Desktop has been launched).
    if not resolved_config.parent.exists():
        raise ClaudeInstallError(
            f"Config directory {resolved_config.parent} does not exist. "
            "Is Claude Desktop installed and has it been launched at least once?"
        )

    # Refuse to write through symlinks.
    if resolved_config.is_symlink():
        raise ClaudeInstallError(
            f"Config file {resolved_config} is a symlink. "
            "Refusing to write through a symlink."
        )

    # Config path, if it exists, must be a regular file.
    if resolved_config.exists() and not resolved_config.is_file():
        raise ClaudeInstallError(
            f"Config path {resolved_config} exists but is not a regular file. "
            "Refusing to write."
        )

    # ------------------------------------------------------------------
    # 4.  Read and parse existing JSON
    # ------------------------------------------------------------------

    existing_content: str = ""
    existing_data: TomlTable = {}

    if resolved_config.exists():
        try:
            existing_content = resolved_config.read_text(encoding="utf-8")
        except OSError as exc:
            raise ClaudeInstallError(
                f"Cannot read config file {resolved_config}: {exc}"
            ) from exc

        try:
            existing_data = _json.loads(existing_content)
        except _json.JSONDecodeError as exc:
            raise ClaudeInstallError(
                f"Config file {resolved_config} cannot be parsed as valid JSON: {exc}"
            ) from exc

        if not isinstance(existing_data, dict):
            raise ClaudeInstallError(
                f"Config file {resolved_config}: top-level value is not a JSON object."
            )

        if CLAUDE_TOP_LEVEL_KEY in existing_data and not isinstance(
            existing_data[CLAUDE_TOP_LEVEL_KEY], dict
        ):
            raise ClaudeInstallError(
                f"Config file {resolved_config}: "
                f"'{CLAUDE_TOP_LEVEL_KEY}' is present but is not a JSON object."
            )

    # ------------------------------------------------------------------
    # 5.  Idempotency / conflict check
    # ------------------------------------------------------------------

    mcp_servers_value = existing_data.get(CLAUDE_TOP_LEVEL_KEY, {})
    if not isinstance(mcp_servers_value, dict):
        raise ClaudeInstallError(
            f"Config file {resolved_config}: "
            f"'{CLAUDE_TOP_LEVEL_KEY}' is present but is not a JSON object."
        )
    mcp_servers = cast(TomlTable, mcp_servers_value)

    if CLAUDE_SERVER_NAME in mcp_servers:
        existing_entry_value = mcp_servers[CLAUDE_SERVER_NAME]
        if not isinstance(existing_entry_value, dict):
            raise ClaudeInstallError(
                f"Config file {resolved_config}: {contract.idempotency_key} "
                "is present but is not a JSON object."
            )
        existing_entry = cast(TomlTable, existing_entry_value)
        if (
            existing_entry.get("type") == CLAUDE_TRANSPORT
            and existing_entry.get("url") == sse_url
        ):
            print(
                f"Already installed: {contract.idempotency_key} in {resolved_config}",
                file=out,
            )
            return "already_installed"
        else:
            raise ClaudeInstallError(
                f"Config file {resolved_config}: {contract.idempotency_key} "
                "already exists with an incompatible transport or url. "
                "Remove the existing entry and rerun."
            )

    # ------------------------------------------------------------------
    # 6.  Confirmation prompt
    # ------------------------------------------------------------------

    if not yes:
        print(f"Target: {resolved_config}", file=out)
        print("Would add:", file=out)
        print(snippet, end="", file=out)
        print(file=out)
        try:
            response = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            response = ""
        if response not in ("y", "yes"):
            print("Cancelled.", file=out)
            return "declined"

    # ------------------------------------------------------------------
    # 7.  Backup existing file
    # ------------------------------------------------------------------

    backup_path: Path | None = None
    if resolved_config.exists():
        timestamp = _timestamp if _timestamp is not None else _make_timestamp()
        backup_suffix = CLAUDE_BACKUP_SUFFIX_TEMPLATE.format(timestamp=timestamp)
        backup_path = resolved_config.parent / (resolved_config.name + backup_suffix)
        try:
            _atomic_write_text(backup_path, existing_content)
        except OSError as exc:
            raise ClaudeInstallError(
                f"Failed to create backup {backup_path}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # 8.  Merge new entry and write atomically
    # ------------------------------------------------------------------

    new_data: TomlTable = {**existing_data}
    if CLAUDE_TOP_LEVEL_KEY not in new_data:
        new_data[CLAUDE_TOP_LEVEL_KEY] = {}
    new_data[CLAUDE_TOP_LEVEL_KEY][CLAUDE_SERVER_NAME] = new_entry
    new_content = _json.dumps(new_data, indent=2) + "\n"

    try:
        _atomic_write_text(resolved_config, new_content)
    except OSError as exc:
        raise ClaudeInstallError(
            f"Failed to write config file {resolved_config}: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # 9.  Post-write validation (restore backup on failure)
    # ------------------------------------------------------------------

    validation_error: Exception | None = None
    try:
        validated = _json.loads(resolved_config.read_text(encoding="utf-8"))
        server_entry = validated.get(CLAUDE_TOP_LEVEL_KEY, {}).get(CLAUDE_SERVER_NAME)
        if server_entry is None:
            raise ValueError(
                f"Post-write validation: {contract.idempotency_key} not found"
            )
        if server_entry.get("type") != CLAUDE_TRANSPORT:
            raise ValueError(
                f"Post-write validation: type mismatch "
                f"(expected {CLAUDE_TRANSPORT!r}, got {server_entry.get('type')!r})"
            )
        if server_entry.get("url") != sse_url:
            raise ValueError(
                f"Post-write validation: url mismatch "
                f"(expected {sse_url!r}, got {server_entry.get('url')!r})"
            )
    except Exception as exc:
        validation_error = exc

    if validation_error is not None:
        # Attempt to restore the pre-install state.
        restore_msg = ""
        if backup_path is not None and backup_path.exists():
            # Prior config existed — restore it from the backup.
            try:
                _atomic_write_text(
                    resolved_config, backup_path.read_text(encoding="utf-8")
                )
                restore_msg = f" Config restored from backup {backup_path}."
            except OSError:
                restore_msg = (
                    f" Backup restore failed"
                    f" — please restore manually from {backup_path}."
                )
        else:
            # No prior config existed — remove the freshly-written (invalid) file.
            with contextlib.suppress(OSError):
                resolved_config.unlink(missing_ok=True)
            restore_msg = " Freshly-written config removed (no prior file to restore)."
        raise ClaudeInstallError(
            f"Post-write validation failed: {validation_error}.{restore_msg}"
        ) from validation_error

    # ------------------------------------------------------------------
    # 10.  Success
    # ------------------------------------------------------------------

    print(
        f"Installed: {contract.idempotency_key} → {resolved_config}",
        file=out,
    )
    return "installed"
