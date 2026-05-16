"""Claude Code agent target contract for Modal MCP install operations.

This module specifies *exactly* how ``modal-mcp`` registers itself in the
Claude Code CLI configuration file (``~/.claude/settings.json``).  No file is
written here; the contract is a frozen dataclass that install and print code
can read, and that tests can assert before any write reaches the user's
filesystem.

Install contract summary
------------------------

.. list-table::

   * - Config path
     - ``~/.claude/settings.json`` (platform-agnostic; same path on macOS,
       Windows, and Linux).
   * - Format
     - JSON
   * - Transport
     - stdio — Claude Code launches ``modal-mcp`` directly as a subprocess.
       The ``command`` and ``args`` fields in the ``mcpServers.modal-mcp``
       entry specify the invocation.
   * - Top-level key
     - ``mcpServers`` (camelCase, as per Claude Code / Claude Desktop schema)
   * - Server name / merge key
     - ``modal-mcp``
   * - Idempotency key
     - ``mcpServers.modal-mcp``
   * - Backup suffix
     - ``.bak.{timestamp}`` where ``{timestamp}`` is substituted with a
       filesystem-safe compact UTC datetime, e.g. ``.bak.20260419T103000``.
       Compact format (no colons, no spaces) ensures the resulting filename is
       valid on Windows (NTFS/FAT32 forbid ``:``) as well as macOS and Linux.
   * - Cwd / env-file strategy
     - ``absolute_path_flag``: the install merges
       ``args = ["stdio", "--env-file", "<absolute-path>"]`` so that Claude
       Code can find the ``.env`` file regardless of the working directory at
       launch.
   * - Parse validation
     - ``json.loads`` round-trip; assert ``mcpServers.modal-mcp`` is present
       with the expected ``command`` and ``args`` values.

Generated config block
----------------------

The installer merges the following entry under the ``mcpServers`` key::

    "modal-mcp": {
        "command": "modal-mcp",
        "args": ["stdio", "--env-file", "/absolute/path/to/.env"]
    }

If ``mcpServers`` does not yet exist it is created.  If an identically-keyed
entry already contains the correct ``command`` and ``args`` the install is a
no-op.

Claude Code config schema reference
-----------------------------------

Claude Code's ``~/.claude/settings.json`` reuses the Claude Desktop JSON
schema for the ``mcpServers`` mapping, but differs in two ways:

1. The path is platform-agnostic: macOS, Windows, and Linux all use
   ``~/.claude/settings.json`` (Claude Code is a CLI; no OS-specific
   ``Application Support``/``AppData`` directory is involved).
2. The transport is stdio: Claude Code launches each MCP server as a
   subprocess using the ``command`` + ``args`` shape (the same shape Codex
   uses).  There is no ``{type: "sse", url: ...}`` HTTP form in Claude Code
   entries — the SSE form is Claude Desktop-only.

See https://docs.claude.com/en/docs/claude-code (Settings) for the JSON shape
of the ``mcpServers`` mapping and per-server entries.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TextIO, cast

from modal_mcp.agent_targets.contract import AgentTargetContract

#: Internal alias used by the install body.
JsonTable = dict[str, Any]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Human-readable agent identifier.
CLAUDE_CODE_AGENT_NAME: Final[str] = "claude_code"

#: Filename of the Claude Code configuration file.
CLAUDE_CODE_CONFIG_FILENAME: Final[str] = "settings.json"

#: Representative config path (platform-agnostic).
CLAUDE_CODE_CONFIG_PATH: Final[Path] = Path("~/.claude/settings.json")

#: File format written and read by the installer.
CLAUDE_CODE_CONFIG_FORMAT: Final = "json"

#: MCP transport type.  Claude Code launches MCP servers as subprocesses via
#: stdio.
CLAUDE_CODE_TRANSPORT: Final = "stdio"

#: Registration name for the server inside ``mcpServers``.
CLAUDE_CODE_SERVER_NAME: Final[str] = "modal-mcp"

#: Top-level JSON key under which MCP server registrations are merged.
#: Uses camelCase as required by the Claude Code / Claude Desktop schema.
CLAUDE_CODE_TOP_LEVEL_KEY: Final[str] = "mcpServers"

#: Dotted config path used for idempotency checks.
CLAUDE_CODE_IDEMPOTENCY_KEY: Final[str] = "mcpServers.modal-mcp"

#: Default command used to invoke the ``modal-mcp`` server process.
CLAUDE_CODE_SERVER_COMMAND: Final[str] = "modal-mcp"

#: Argument list template for stdio launch.  ``{env_file}`` must be replaced
#: with the *absolute* path to the ``.env`` file at install time.
CLAUDE_CODE_SERVER_ARGS_TEMPLATE: Final[tuple[str, ...]] = (
    "stdio",
    "--env-file",
    "{env_file}",
)

#: Backup filename suffix template.  ``{timestamp}`` is substituted with a
#: filesystem-safe compact UTC datetime at write time, e.g.
#: ``.bak.20260419T103000``.  The compact format (``YYYYMMDDTHHmmss``) avoids
#: the ``:`` character that is illegal in filenames on Windows NTFS/FAT32.
CLAUDE_CODE_BACKUP_SUFFIX_TEMPLATE: Final[str] = ".bak.{timestamp}"

#: Absolute env-file placeholder used when no path is supplied to
#: :func:`format_config_snippet`.  The leading ``/`` makes the placeholder
#: recognisably absolute so users understand they must supply a real path.
_ENV_FILE_PLACEHOLDER: Final[str] = "/absolute/path/to/.env"

# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------


def build_contract(command: str = CLAUDE_CODE_SERVER_COMMAND) -> AgentTargetContract:
    """Construct the canonical Claude Code install contract.

    Args:
        command: The executable used to launch ``modal-mcp``.  Defaults to
            :data:`CLAUDE_CODE_SERVER_COMMAND` (``"modal-mcp"``).  Pass an
            absolute path if ``modal-mcp`` is installed outside the system
            PATH, e.g. ``"/home/user/.local/bin/modal-mcp"``.

    Returns:
        A frozen :class:`AgentTargetContract` instance.
    """
    return AgentTargetContract(
        # --- identity ---
        agent_name=CLAUDE_CODE_AGENT_NAME,
        # --- config location ---
        # ~/.claude/settings.json is platform-agnostic: Claude Code uses the
        # same location on macOS, Windows, and Linux.
        representative_config_path=CLAUDE_CODE_CONFIG_PATH,
        config_format=CLAUDE_CODE_CONFIG_FORMAT,
        # --- MCP transport ---
        # Claude Code uses stdio for all MCP server connections.  It launches
        # the server as a subprocess using the specified command and args.
        mcp_transport=CLAUDE_CODE_TRANSPORT,
        server_name=CLAUDE_CODE_SERVER_NAME,
        top_level_key=CLAUDE_CODE_TOP_LEVEL_KEY,
        # --- transport-specific ---
        server_url=None,  # not used for stdio transport
        server_command=command,
        server_args_template=CLAUDE_CODE_SERVER_ARGS_TEMPLATE,
        # --- cwd / env-file ---
        # stdio transport: Claude Code launches modal-mcp directly, so the
        # env-file path must be embedded in the args list.  Install code
        # substitutes the absolute path for '{env_file}' in
        # server_args_template.
        env_file_strategy="absolute_path_flag",
        supports_cwd_config=False,
        # --- install mechanics ---
        # Backup: settings.json.bak.20260419T103000
        # Compact format avoids ':' which is illegal on Windows NTFS/FAT32.
        backup_suffix_template=CLAUDE_CODE_BACKUP_SUFFIX_TEMPLATE,
        refusal_conditions=(
            "config directory (~/.claude) does not exist "
            "(Claude Code not installed or never launched)",
            "config file (~/.claude/settings.json) is a symlink "
            "(refused to write through symlink)",
            "config file exists but cannot be parsed as valid JSON",
            "parsed config top-level value is not a JSON object",
            "mcpServers value is present but is not a JSON object",
            "config file or directory is not readable or writable "
            "(permission denied — check file ownership after user switch)",
            "disk full or out of space (ENOSPC) during atomic temp-file write",
            "config path exists but is not a regular file "
            "(directory, symlink, FIFO, socket, or device node)",
            "mcpServers.modal-mcp already exists with an incompatible command "
            "or args (e.g. a different executable or missing --env-file flag)",
        ),
        parse_validation_strategy=(
            "after writing, reload the file with json.loads; "
            "assert the result is a dict that contains mcpServers.modal-mcp "
            f"with command='{command}' and args starting with 'stdio'; "
            "restore backup and raise if validation fails"
        ),
        dry_run_description=(
            f"would add mcpServers.modal-mcp "
            f"(command: {command}, args: stdio --env-file <absolute-path>) "
            "to ~/.claude/settings.json"
        ),
        idempotency_key=CLAUDE_CODE_IDEMPOTENCY_KEY,
    )


#: Default contract for display, testing, and documentation purposes.
#:
#: .. warning::
#:
#:     **Install code must not import this singleton when modal-mcp is not
#:     on the system PATH.**  It is frozen with
#:     :data:`CLAUDE_CODE_SERVER_COMMAND` (``"modal-mcp"``).  Call
#:     ``build_contract(command=resolved_command)`` for non-PATH installs.
CONTRACT: Final[AgentTargetContract] = build_contract()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class ClaudeCodeInstallError(Exception):
    """Raised when a Claude Code config install fails a safety check.

    Possible causes include (but are not limited to):
    - Config directory does not exist (Claude Code not installed).
    - Config file is a symlink (refused to follow).
    - Config file is not a regular file (directory, device, FIFO …).
    - Config file cannot be parsed as valid JSON.
    - Top-level config value is not a JSON object.
    - ``mcpServers`` value is present but is not a JSON object.
    - ``mcpServers.modal-mcp`` already exists with an incompatible entry.
    - Post-write validation failed (backup restored automatically).
    """


def _make_timestamp() -> str:
    """Return a compact UTC datetime string suitable for backup filenames."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically write text *content* to *path* via a sibling temp file."""
    encoded = content.encode()
    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_claude_code_")
        tmp_path = Path(tmp_str)
        try:
            offset = 0
            while offset < len(encoded):
                offset += os.write(fd, encoded[offset:])
        finally:
            os.close(fd)
        tmp_path.replace(path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def _build_entry(env_file: str, command: str) -> JsonTable:
    """Construct the ``mcpServers.modal-mcp`` JSON entry."""
    rendered_args = [
        arg.format(env_file=env_file) if "{env_file}" in arg else arg
        for arg in CLAUDE_CODE_SERVER_ARGS_TEMPLATE
    ]
    return {"command": command, "args": rendered_args}


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def format_config_snippet(
    env_file: str | Path | None = None,
    command: str = CLAUDE_CODE_SERVER_COMMAND,
) -> str:
    """Render the Claude Code JSON config block as a string.

    The returned string is a ready-to-paste JSON object that should be merged
    into ``~/.claude/settings.json`` under the existing top-level keys.

    Args:
        env_file: Absolute path to the ``.env`` file.  If ``None``, the
            descriptive placeholder ``/absolute/path/to/.env`` is used so that
            the emitted snippet is still syntactically valid JSON.
        command: The ``modal-mcp`` executable.  Defaults to
            :data:`CLAUDE_CODE_SERVER_COMMAND` (``"modal-mcp"``).

    Returns:
        A JSON snippet string containing the ``mcpServers.modal-mcp`` entry.

    Raises:
        ValueError: If *env_file* is given as a non-absolute path.
    """
    if env_file is None:
        env_path_str = _ENV_FILE_PLACEHOLDER
    else:
        env_file_path = Path(env_file)
        if not env_file_path.is_absolute():
            msg = f"env_file must be an absolute path; got: {env_file!r}"
            raise ValueError(msg)
        env_path_str = str(env_file_path)

    config_block: dict[str, object] = {
        CLAUDE_CODE_TOP_LEVEL_KEY: {
            CLAUDE_CODE_SERVER_NAME: _build_entry(env_path_str, command),
        }
    }
    return json.dumps(config_block, indent=2) + "\n"


def render(
    *,
    env_file: str | Path | None = None,
    command: str | None = None,
    file: TextIO | None = None,
) -> str:
    """Render the Claude Code JSON config snippet.

    When *file* is not None, prints the comment header and snippet to *file*.
    Always returns the snippet body so callers can inspect or test it.

    Args:
        env_file: Optional absolute path to the ``.env`` file.  If provided,
            it is embedded in the rendered ``args`` list.  Relative paths
            are rejected with :class:`ValueError`.
        command: Override the ``modal-mcp`` executable name.  Defaults to
            :data:`CLAUDE_CODE_SERVER_COMMAND`.
        file: Optional file-like object to write the snippet (with comment
            header) to.  When ``None`` only the snippet body is returned.

    Returns:
        The rendered JSON snippet.

    Raises:
        ValueError: If *env_file* is a non-absolute path.
    """
    resolved_command = command if command is not None else CLAUDE_CODE_SERVER_COMMAND
    snippet = format_config_snippet(env_file=env_file, command=resolved_command)
    if file is not None:
        # Emit pure JSON only; callers (CLI, tests) can re-parse the buffer.
        # Comment lines would invalidate JSON consumers — Claude Code's
        # settings.json does not support // comments.
        print(snippet, end="", file=file)
    return snippet


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def install(
    *,
    env_file: str | Path,
    command: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
    config_path_override: Path | None = None,
    file: TextIO | None = None,
    _timestamp: str | None = None,
) -> str:
    """Install the ``mcpServers.modal-mcp`` entry into ``~/.claude/settings.json``.

    Supports dry-run previews, interactive confirmation, atomic backup,
    idempotent re-runs, and post-write validation.

    Args:
        env_file: *Absolute* path to the ``.env`` file.  Embedded in the
            ``args`` list as ``--env-file <path>``.
        command: The ``modal-mcp`` executable to register.  Defaults to
            :data:`CLAUDE_CODE_SERVER_COMMAND`.
        dry_run: When ``True``, preview the change and return ``"dry_run"``
            without touching the filesystem.
        yes: When ``True``, skip the interactive confirmation prompt.
        config_path_override: Override the default install target
            (``~/.claude/settings.json``).  Useful in tests.
        file: Output stream for status messages.  Defaults to ``sys.stdout``.
        _timestamp: Override the timestamp embedded in the backup suffix.
            Intended for deterministic tests only.

    Returns:
        One of ``"installed"``, ``"already_installed"``, ``"declined"``,
        or ``"dry_run"``.

    Raises:
        ValueError: If *env_file* is not an absolute path.
        ClaudeCodeInstallError: If a safety check fails.
    """
    out = sys.stdout if file is None else file

    # ------------------------------------------------------------------
    # 1.  Validate and resolve arguments
    # ------------------------------------------------------------------

    env_file_path = Path(env_file)
    if not env_file_path.is_absolute():
        msg = f"env_file must be an absolute path; got: {env_file!r}"
        raise ValueError(msg)
    env_path_str = str(env_file_path)

    resolved_command = command if command is not None else CLAUDE_CODE_SERVER_COMMAND
    contract = build_contract(resolved_command)

    if config_path_override is None:
        resolved_config = CLAUDE_CODE_CONFIG_PATH.expanduser().absolute()
    else:
        resolved_config = Path(config_path_override).absolute()

    new_entry = _build_entry(env_path_str, resolved_command)
    snippet = format_config_snippet(env_file=env_path_str, command=resolved_command)

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

    if not resolved_config.parent.exists():
        raise ClaudeCodeInstallError(
            f"Config directory {resolved_config.parent} does not exist. "
            "Is Claude Code installed and has it been run at least once?"
        )

    if resolved_config.is_symlink():
        raise ClaudeCodeInstallError(
            f"Config file {resolved_config} is a symlink. "
            "Refusing to write through a symlink."
        )

    if resolved_config.exists() and not resolved_config.is_file():
        raise ClaudeCodeInstallError(
            f"Config path {resolved_config} exists but is not a regular file. "
            "Refusing to write."
        )

    # ------------------------------------------------------------------
    # 4.  Read and parse existing JSON
    # ------------------------------------------------------------------

    existing_content: str = ""
    existing_data: JsonTable = {}

    if resolved_config.exists():
        try:
            existing_content = resolved_config.read_text(encoding="utf-8")
        except OSError as exc:
            raise ClaudeCodeInstallError(
                f"Cannot read config file {resolved_config}: {exc}"
            ) from exc

        try:
            existing_data = json.loads(existing_content)
        except json.JSONDecodeError as exc:
            raise ClaudeCodeInstallError(
                f"Config file {resolved_config} cannot be parsed as valid JSON: {exc}"
            ) from exc

        if not isinstance(existing_data, dict):
            raise ClaudeCodeInstallError(
                f"Config file {resolved_config}: top-level value is not a JSON object."
            )

        if CLAUDE_CODE_TOP_LEVEL_KEY in existing_data and not isinstance(
            existing_data[CLAUDE_CODE_TOP_LEVEL_KEY], dict
        ):
            raise ClaudeCodeInstallError(
                f"Config file {resolved_config}: "
                f"'{CLAUDE_CODE_TOP_LEVEL_KEY}' is present but is not a JSON object."
            )

    # ------------------------------------------------------------------
    # 5.  Idempotency / conflict check
    # ------------------------------------------------------------------

    mcp_servers_value = existing_data.get(CLAUDE_CODE_TOP_LEVEL_KEY, {})
    if not isinstance(mcp_servers_value, dict):
        raise ClaudeCodeInstallError(
            f"Config file {resolved_config}: "
            f"'{CLAUDE_CODE_TOP_LEVEL_KEY}' is present but is not a JSON object."
        )
    mcp_servers = cast(JsonTable, mcp_servers_value)
    if CLAUDE_CODE_SERVER_NAME in mcp_servers:
        existing_entry_value = mcp_servers[CLAUDE_CODE_SERVER_NAME]
        if not isinstance(existing_entry_value, dict):
            raise ClaudeCodeInstallError(
                f"Config file {resolved_config}: {contract.idempotency_key} "
                "is present but is not a JSON object."
            )
        existing_entry = cast(JsonTable, existing_entry_value)
        if (
            existing_entry.get("command") == new_entry["command"]
            and existing_entry.get("args") == new_entry["args"]
        ):
            print(
                f"Already installed: {contract.idempotency_key} in {resolved_config}",
                file=out,
            )
            return "already_installed"
        # Existing entry with different command/args: not a hard refusal —
        # back up and replace, mirroring the way an explicit re-install
        # overwrites a prior entry.  This matches the test
        # ``test_install_creates_backup_when_replacing``.

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
        backup_suffix = CLAUDE_CODE_BACKUP_SUFFIX_TEMPLATE.format(timestamp=timestamp)
        backup_path = resolved_config.parent / (resolved_config.name + backup_suffix)
        try:
            _atomic_write_text(backup_path, existing_content)
        except OSError as exc:
            raise ClaudeCodeInstallError(
                f"Failed to create backup {backup_path}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # 8.  Merge new entry and write atomically
    # ------------------------------------------------------------------

    new_data: JsonTable = {**existing_data}
    if CLAUDE_CODE_TOP_LEVEL_KEY not in new_data:
        new_data[CLAUDE_CODE_TOP_LEVEL_KEY] = {}
    else:
        # Preserve other mcpServers entries by copying the existing mapping.
        new_data[CLAUDE_CODE_TOP_LEVEL_KEY] = {**new_data[CLAUDE_CODE_TOP_LEVEL_KEY]}
    new_data[CLAUDE_CODE_TOP_LEVEL_KEY][CLAUDE_CODE_SERVER_NAME] = new_entry
    new_content = json.dumps(new_data, indent=2) + "\n"

    try:
        _atomic_write_text(resolved_config, new_content)
    except OSError as exc:
        raise ClaudeCodeInstallError(
            f"Failed to write config file {resolved_config}: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # 9.  Post-write validation (restore backup on failure)
    # ------------------------------------------------------------------

    validation_error: Exception | None = None
    try:
        validated = json.loads(resolved_config.read_text(encoding="utf-8"))
        server_entry = validated.get(CLAUDE_CODE_TOP_LEVEL_KEY, {}).get(
            CLAUDE_CODE_SERVER_NAME
        )
        if server_entry is None:
            raise ValueError(
                f"Post-write validation: {contract.idempotency_key} not found"
            )
        if server_entry.get("command") != new_entry["command"]:
            raise ValueError(
                f"Post-write validation: command mismatch "
                f"(expected {new_entry['command']!r}, "
                f"got {server_entry.get('command')!r})"
            )
        if server_entry.get("args") != new_entry["args"]:
            raise ValueError(
                f"Post-write validation: args mismatch "
                f"(expected {new_entry['args']!r}, got {server_entry.get('args')!r})"
            )
    except Exception as exc:
        validation_error = exc

    if validation_error is not None:
        restore_msg = ""
        if backup_path is not None and backup_path.exists():
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
            with contextlib.suppress(OSError):
                resolved_config.unlink(missing_ok=True)
            restore_msg = " Freshly-written config removed (no prior file to restore)."
        raise ClaudeCodeInstallError(
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


#: Concrete exception class raised by :func:`install` on safety-check failure.
#:
#: Exposed for the CLI layer so it can catch the install error without
#: importing the concrete class by name (which couples the CLI to per-target
#: module internals).  Every agent target module must expose this attribute.
INSTALL_ERROR: Final[type[Exception]] = ClaudeCodeInstallError


def install_from_cli(
    args: argparse.Namespace,
    *,
    config_path_override: Path | None = None,
) -> int:
    """Run the Claude Code install from a parsed CLI ``args`` namespace.

    Returns an exit code (0 on success, 1 on install failure).  Handles the
    Claude Code-specific argument parsing (absolute ``--env-file`` enforcement)
    and exception mapping so that ``cli/setup.py`` does not need any
    per-target branching.

    Args:
        args: Parsed CLI namespace.
        config_path_override: Optional override for the config destination
            (e.g. used by tests).  Forwarded to :func:`install`.
    """
    from modal_mcp.setup import DEFAULT_ENV_FILE

    env_file_arg: str | None = getattr(args, "env_file", None)
    dry_run: bool = getattr(args, "dry_run", False)
    yes: bool = getattr(args, "yes", False)

    if env_file_arg is not None:
        candidate = Path(env_file_arg).expanduser()
        # Claude Code (stdio) embeds the env-file path verbatim, so it must be
        # absolute.  Silently calling .absolute() on a relative input would
        # resolve against the CWD of the install process — almost certainly
        # wrong when Claude Code later launches modal-mcp from its own CWD.
        if not candidate.is_absolute():
            print(
                f"error: --env-file must be an absolute path for"
                f" --install claude-code; got: {env_file_arg!r}",
                file=sys.stderr,
            )
            return 1
        resolved_env = candidate.absolute()
    else:
        resolved_env = Path(DEFAULT_ENV_FILE).expanduser().absolute()

    try:
        install(
            env_file=resolved_env,
            dry_run=dry_run,
            yes=yes,
            config_path_override=config_path_override,
        )
    except (ValueError, INSTALL_ERROR) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


__all__ = [
    "CLAUDE_CODE_AGENT_NAME",
    "CLAUDE_CODE_BACKUP_SUFFIX_TEMPLATE",
    "CLAUDE_CODE_CONFIG_FILENAME",
    "CLAUDE_CODE_CONFIG_FORMAT",
    "CLAUDE_CODE_CONFIG_PATH",
    "CLAUDE_CODE_IDEMPOTENCY_KEY",
    "CLAUDE_CODE_SERVER_ARGS_TEMPLATE",
    "CLAUDE_CODE_SERVER_COMMAND",
    "CLAUDE_CODE_SERVER_NAME",
    "CLAUDE_CODE_TOP_LEVEL_KEY",
    "CLAUDE_CODE_TRANSPORT",
    "CONTRACT",
    "INSTALL_ERROR",
    "AgentTargetContract",
    "ClaudeCodeInstallError",
    "build_contract",
    "format_config_snippet",
    "install",
    "install_from_cli",
    "render",
]
