"""Codex agent target contract for Modal MCP install operations.

This module specifies *exactly* how ``modal-mcp`` registers itself in the
OpenAI Codex CLI configuration.  No file is written here; the contract is a
frozen dataclass that install and print code can read, and that tests can assert
before any write reaches the user's filesystem.

Install contract summary
------------------------

.. list-table::

   * - Config path
     - ``~/.codex/config.toml``
   * - Format
     - TOML
   * - Transport
     - stdio — Codex launches ``modal-mcp`` directly as a subprocess.
       The ``command`` and ``args`` fields in the ``[mcp_servers.modal-mcp]``
       section specify the invocation.
   * - Top-level key
     - ``mcp_servers`` (snake_case, as per Codex CLI schema)
   * - Server name / merge key
     - ``modal-mcp``
   * - Idempotency key
     - ``mcp_servers.modal-mcp``
   * - Backup suffix
     - ``.bak.{timestamp}`` where ``{timestamp}`` is substituted with a
       filesystem-safe compact UTC datetime, e.g. ``.bak.20260419T103000``.
       Compact format (no colons, no spaces) ensures the resulting filename is
       valid on Windows (NTFS/FAT32 forbid ``:``) as well as macOS and Linux.
   * - Cwd / env-file strategy
     - ``absolute_path_flag``: the install merges
       ``args = ["stdio", "--env-file", "<absolute-path>"]`` so that Codex can
       find the ``.env`` file regardless of the working directory at launch.
   * - Refusal conditions
     - Nine conditions listed in :data:`CODEX_CONTRACT`.
   * - Parse validation
     - ``tomllib.loads`` round-trip; assert ``mcp_servers.modal-mcp`` table
       is present with the expected ``command`` and ``args`` values.

Generated config block
----------------------

The installer merges the following entry under the ``mcp_servers`` key::

    [mcp_servers.modal-mcp]
    command = "modal-mcp"
    args = ["stdio", "--env-file", "/absolute/path/to/.env"]

If ``mcp_servers`` does not yet exist it is created.  If an identically-keyed
entry already contains the correct ``command`` and ``args`` the install is a
no-op.

Codex CLI config schema reference
----------------------------------

The ``command`` + ``args`` shape inside ``[mcp_servers.<name>]`` is the format
documented for the OpenAI Codex CLI at:
https://github.com/openai/codex/blob/main/docs/config.md

Each MCP server entry supports: ``command``, ``args``, ``env`` (optional),
``default_tools_approval_mode`` (optional), and
``supports_parallel_tool_calls`` (optional).  The ``mcp_servers`` top-level
key is always snake_case.  There is no ``type: http`` or ``url`` shape in the
Codex config schema — all servers use stdio subprocess launch.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TextIO, cast

from modal_mcp.agent_targets.contract import AgentTargetContract

#: Internal alias used by the install body (kept ``TomlTable`` for readability).
TomlTable = dict[str, Any]

#: Absolute env-file placeholder used when no path is supplied to
#: :func:`format_config_snippet`.  The leading ``/`` makes the placeholder
#: recognisably absolute so users understand they must supply a real path.
_ENV_FILE_PLACEHOLDER: Final[str] = "/path/to/project/.env"

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Human-readable agent identifier.
CODEX_AGENT_NAME: Final[str] = "codex"

#: Filename of the Codex CLI configuration file.
CODEX_CONFIG_FILENAME: Final[str] = "config.toml"

#: File format written and read by the installer.
CODEX_CONFIG_FORMAT: Final = "toml"

#: MCP transport type.  Codex launches MCP servers as subprocesses via stdio.
CODEX_TRANSPORT: Final = "stdio"

#: Registration name for the server inside ``mcp_servers``.
CODEX_SERVER_NAME: Final[str] = "modal-mcp"

#: Top-level TOML key under which MCP server registrations are merged.
#: Uses snake_case as required by the Codex CLI config schema.
CODEX_TOP_LEVEL_KEY: Final[str] = "mcp_servers"

#: Dotted config path used for idempotency checks.
CODEX_IDEMPOTENCY_KEY: Final[str] = "mcp_servers.modal-mcp"

#: Default command used to invoke the ``modal-mcp`` server process.
CODEX_SERVER_COMMAND: Final[str] = "modal-mcp"

#: Argument list template for stdio launch.
#: ``{env_file}`` must be replaced with the *absolute* path to the ``.env``
#: file at install time.
CODEX_SERVER_ARGS_TEMPLATE: Final[tuple[str, ...]] = (
    "stdio",
    "--env-file",
    "{env_file}",
)

#: Backup filename suffix template.  ``{timestamp}`` is substituted with a
#: filesystem-safe compact UTC datetime at write time, e.g.
#: ``.bak.20260419T103000``.  The compact format (``YYYYMMDDTHHmmss``) avoids
#: the ``:`` character that is illegal in filenames on Windows NTFS/FAT32.
CODEX_BACKUP_SUFFIX_TEMPLATE: Final[str] = ".bak.{timestamp}"

# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------


def build_contract(command: str = CODEX_SERVER_COMMAND) -> AgentTargetContract:
    """Construct the canonical Codex CLI install contract.

    Args:
        command: The executable used to launch ``modal-mcp``.  Defaults to
            ``CODEX_SERVER_COMMAND`` (``"modal-mcp"``).  Pass an absolute path
            if ``modal-mcp`` is installed outside the system PATH, e.g.
            ``"/home/user/.local/bin/modal-mcp"``.

    Returns:
        A frozen :class:`AgentTargetContract` instance.

    .. important::

        Install code MUST call this function with ``command`` resolved from the
        runtime environment rather than importing :data:`CODEX_CONTRACT`
        directly when ``modal-mcp`` is not on the system PATH.  The
        ``server_command``, ``parse_validation_strategy``, and
        ``dry_run_description`` all embed the command so that any consumer
        (installer, dry-run printer, validator) sees a consistent value.
        Do NOT use the module-level :data:`CODEX_CONTRACT` singleton when the
        resolved command differs from :data:`CODEX_SERVER_COMMAND`.
    """
    return AgentTargetContract(
        # --- identity ---
        agent_name=CODEX_AGENT_NAME,
        # --- config location ---
        # ~/.codex/config.toml is platform-agnostic: Codex uses the same
        # location on macOS, Windows, and Linux.  The file is optional;
        # Codex creates it with default content on first run.
        representative_config_path=Path("~/.codex/config.toml"),
        config_format=CODEX_CONFIG_FORMAT,
        # --- MCP transport ---
        # Codex uses stdio for all MCP server connections.  It launches the
        # server as a subprocess using the specified command and args.
        mcp_transport=CODEX_TRANSPORT,
        server_name=CODEX_SERVER_NAME,
        top_level_key=CODEX_TOP_LEVEL_KEY,
        # --- transport-specific ---
        server_url=None,  # not used for stdio transport
        server_command=command,
        server_args_template=CODEX_SERVER_ARGS_TEMPLATE,
        # --- cwd / env-file ---
        # stdio transport: Codex launches modal-mcp directly, so the env-file
        # path must be embedded in the args list.  Install code substitutes
        # the absolute path for '{env_file}' in server_args_template.
        env_file_strategy="absolute_path_flag",
        supports_cwd_config=False,
        # --- install mechanics ---
        # Backup: config.toml.bak.20260419T103000
        # Compact format avoids ':' which is illegal on Windows NTFS/FAT32.
        backup_suffix_template=CODEX_BACKUP_SUFFIX_TEMPLATE,
        refusal_conditions=(
            "config directory (~/.codex) does not exist "
            "(Codex CLI not installed or never launched)",
            "config file (~/.codex/config.toml) is a symlink "
            "(refused to write through symlink)",
            "config file exists but cannot be parsed as valid TOML",
            "parsed config top-level value is not a TOML table (mapping)",
            "mcp_servers value is present but is not a TOML table",
            "config file or directory is not readable or writable "
            "(permission denied — check file ownership after user switch)",
            "disk full or out of space (ENOSPC) during atomic temp-file write",
            "config path exists but is not a regular file "
            "(directory, symlink, FIFO, socket, or device node)",
            "mcp_servers.modal-mcp already exists with an incompatible command "
            "or args (e.g. a different executable or missing --env-file flag)",
        ),
        # Validation: after writing, reload with tomllib.loads and assert the
        # expected structure is present.  The original file is restored from
        # the backup if validation fails.
        parse_validation_strategy=(
            "after writing, reload the file with tomllib.loads; "
            "assert the result is a table that contains mcp_servers.modal-mcp "
            f"with command='{command}' and args starting with 'stdio'; "
            "restore backup and raise if validation fails"
        ),
        dry_run_description=(
            f"would add [mcp_servers.modal-mcp] "
            f"(command: {command}, args: stdio --env-file <absolute-path>) "
            "to ~/.codex/config.toml"
        ),
        idempotency_key=CODEX_IDEMPOTENCY_KEY,
    )


# ---------------------------------------------------------------------------
# Config snippet renderer
# ---------------------------------------------------------------------------


def format_config_snippet(
    env_file: str | Path | None = None,
    command: str = CODEX_SERVER_COMMAND,
) -> str:
    """Render the Codex TOML config block as a string.

    The returned string is a ready-to-paste ``[mcp_servers.modal-mcp]`` TOML
    block.  It always includes an absolute ``--env-file`` path in the ``args``
    list so that Codex can locate the server settings regardless of its
    working directory at launch.

    This function does not write any files and does not read secrets from the
    environment or filesystem.

    Args:
        env_file: Absolute path to the ``.env`` file.  If ``None``, the
            descriptive placeholder ``/path/to/project/.env`` is used so that
            the emitted snippet is still syntactically valid TOML that a user
            can paste and edit.
        command: The ``modal-mcp`` executable.  Defaults to
            :data:`CODEX_SERVER_COMMAND` (``"modal-mcp"``).

    Returns:
        A TOML snippet string containing the ``[mcp_servers.modal-mcp]``
        section with ``command`` and ``args`` fields.

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

    def _escape_toml(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    rendered_args = [
        arg.format(env_file=env_path_str) if "{env_file}" in arg else arg
        for arg in CODEX_SERVER_ARGS_TEMPLATE
    ]
    args_toml = ", ".join(f'"{_escape_toml(a)}"' for a in rendered_args)
    return (
        f"[{CODEX_TOP_LEVEL_KEY}.{CODEX_SERVER_NAME}]\n"
        f'command = "{_escape_toml(command)}"\n'
        f"args = [{args_toml}]\n"
    )


#: Default contract for display, testing, and documentation purposes.
#:
#: .. warning::
#:
#:     **Install code must not import this singleton when modal-mcp is not
#:     on the system PATH.**  It is frozen with
#:     :data:`CODEX_SERVER_COMMAND` (``"modal-mcp"``).  If the user's
#:     ``modal-mcp`` executable lives at a non-default absolute path, the
#:     ``server_command``, ``parse_validation_strategy``, and
#:     ``dry_run_description`` embedded in this singleton will all reference
#:     a command that Codex cannot find.  Install code must call
#:     ``build_contract(command=resolved_command)`` and use the returned instance.
CODEX_CONTRACT: Final[AgentTargetContract] = build_contract()


# ---------------------------------------------------------------------------
# Install + render (absorbed from agent_config.py)
# ---------------------------------------------------------------------------


class CodexInstallError(Exception):
    """Raised when a Codex config install fails a safety check.

    Possible causes include (but are not limited to):
    - Config directory does not exist (Codex CLI not installed).
    - Config file is a symlink (refused to follow).
    - Config file is not a regular file (directory, device, FIFO …).
    - Config file cannot be parsed as valid TOML.
    - Top-level config value is not a TOML table.
    - ``mcp_servers`` value is present but is not a TOML table.
    - ``mcp_servers.modal-mcp`` already exists with an incompatible entry.
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


def render(
    *,
    env_file: str | Path | None = None,
    command: str | None = None,
    file: TextIO | None = None,
) -> str:
    """Render the Codex TOML config snippet.

    When *file* is not None, prints the comment header and snippet to *file*
    (matching the previous ``_print_codex_config`` behaviour).  Always returns
    the snippet body so callers can inspect or test it.

    Args:
        env_file: Optional absolute path to the ``.env`` file. If provided,
            it is embedded in the rendered ``args`` list.  Relative paths
            are rejected with :class:`ValueError`.
        command: Override the ``modal-mcp`` executable name.  Defaults to
            :data:`CODEX_SERVER_COMMAND`.
        file: Optional file-like object to write the snippet (with comment
            header) to.  When ``None`` only the snippet body is returned.

    Returns:
        The rendered TOML snippet.

    Raises:
        ValueError: If *env_file* is a non-absolute path.
    """
    resolved_command = command if command is not None else CODEX_SERVER_COMMAND
    snippet = format_config_snippet(env_file=env_file, command=resolved_command)
    if file is not None:
        print("# Add this block to ~/.codex/config.toml", file=file)
        print(
            "# Transport: stdio (Codex launches modal-mcp as a subprocess)",
            file=file,
        )
        print(snippet, end="", file=file)
    return snippet


def install(
    *,
    env_file: str | Path,
    command: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
    config_path: Path | None = None,
    file: TextIO | None = None,
    _timestamp: str | None = None,
) -> str:
    """Install the ``[mcp_servers.modal-mcp]`` entry into Codex config.

    The function supports dry-run previews, interactive confirmation,
    atomic backup, idempotent re-runs, and post-write validation.

    Args:
        env_file: *Absolute* path to the ``.env`` file.  Embedded in the
            ``args`` list as ``--env-file <path>``.
        command: The ``modal-mcp`` executable to register.  Defaults to
            :data:`CODEX_SERVER_COMMAND`.
        dry_run: When ``True``, print the planned change and return
            ``"dry_run"`` without touching the filesystem.
        yes: When ``True``, skip the interactive confirmation prompt.
        config_path: Override the default install target
            (``~/.codex/config.toml``).  Useful in tests.
        file: Output stream for status messages.  Defaults to ``sys.stdout``.
        _timestamp: Override the timestamp embedded in the backup suffix.
            Intended for deterministic tests only.

    Returns:
        One of ``"installed"``, ``"already_installed"``, ``"declined"``,
        or ``"dry_run"``.

    Raises:
        ValueError: If *env_file* is not an absolute path.
        CodexInstallError: If a safety check fails.
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

    resolved_command = command if command is not None else CODEX_SERVER_COMMAND
    contract = build_contract(resolved_command)

    if config_path is None:
        resolved_config = contract.representative_config_path.expanduser().absolute()
    else:
        resolved_config = Path(config_path).absolute()

    expected_args: list[str] = [
        arg.format(env_file=env_path_str) if "{env_file}" in arg else arg
        for arg in CODEX_SERVER_ARGS_TEMPLATE
    ]

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
        raise CodexInstallError(
            f"Config directory {resolved_config.parent} does not exist. "
            "Is Codex CLI installed and has it been run at least once?"
        )

    if resolved_config.is_symlink():
        raise CodexInstallError(
            f"Config file {resolved_config} is a symlink. "
            "Refusing to write through a symlink."
        )

    if resolved_config.exists() and not resolved_config.is_file():
        raise CodexInstallError(
            f"Config path {resolved_config} exists but is not a regular file. "
            "Refusing to write."
        )

    # ------------------------------------------------------------------
    # 4.  Read and parse existing TOML
    # ------------------------------------------------------------------

    existing_content: str = ""
    existing_data: TomlTable = {}

    if resolved_config.exists():
        try:
            existing_content = resolved_config.read_text(encoding="utf-8")
        except OSError as exc:
            raise CodexInstallError(
                f"Cannot read config file {resolved_config}: {exc}"
            ) from exc

        try:
            existing_data = tomllib.loads(existing_content)
        except tomllib.TOMLDecodeError as exc:
            raise CodexInstallError(
                f"Config file {resolved_config} cannot be parsed as valid TOML: {exc}"
            ) from exc

        if not isinstance(existing_data, dict):
            raise CodexInstallError(
                f"Config file {resolved_config}: top-level value is not a TOML table."
            )

        if CODEX_TOP_LEVEL_KEY in existing_data and not isinstance(
            existing_data[CODEX_TOP_LEVEL_KEY], dict
        ):
            raise CodexInstallError(
                f"Config file {resolved_config}: "
                f"'{CODEX_TOP_LEVEL_KEY}' is present but is not a TOML table."
            )

    # ------------------------------------------------------------------
    # 5.  Idempotency / conflict check
    # ------------------------------------------------------------------

    mcp_servers_value = existing_data.get(CODEX_TOP_LEVEL_KEY, {})
    if not isinstance(mcp_servers_value, dict):
        raise CodexInstallError(
            f"Config file {resolved_config}: "
            f"'{CODEX_TOP_LEVEL_KEY}' is present but is not a TOML table."
        )
    mcp_servers = cast(TomlTable, mcp_servers_value)
    if CODEX_SERVER_NAME in mcp_servers:
        existing_entry_value = mcp_servers[CODEX_SERVER_NAME]
        if not isinstance(existing_entry_value, dict):
            raise CodexInstallError(
                f"Config file {resolved_config}: {contract.idempotency_key} "
                "is present but is not a TOML table."
            )
        existing_entry = cast(TomlTable, existing_entry_value)
        if (
            existing_entry.get("command") == resolved_command
            and existing_entry.get("args") == expected_args
        ):
            print(
                f"Already installed: {contract.idempotency_key} in {resolved_config}",
                file=out,
            )
            return "already_installed"
        raise CodexInstallError(
            f"Config file {resolved_config}: {contract.idempotency_key} "
            "already exists with an incompatible command or args. "
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
        backup_suffix = CODEX_BACKUP_SUFFIX_TEMPLATE.format(timestamp=timestamp)
        backup_path = resolved_config.parent / (resolved_config.name + backup_suffix)
        try:
            _atomic_write_text(backup_path, existing_content)
        except OSError as exc:
            raise CodexInstallError(
                f"Failed to create backup {backup_path}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # 8.  Build new content and write atomically
    # ------------------------------------------------------------------

    if resolved_config.exists():
        trimmed = existing_content.rstrip("\n")
        new_content = trimmed + "\n\n" + snippet
    else:
        new_content = snippet

    try:
        _atomic_write_text(resolved_config, new_content)
    except OSError as exc:
        raise CodexInstallError(
            f"Failed to write config file {resolved_config}: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # 9.  Post-write validation (restore backup on failure)
    # ------------------------------------------------------------------

    validation_error: Exception | None = None
    try:
        validated = tomllib.loads(resolved_config.read_text(encoding="utf-8"))
        server_entry = validated.get(CODEX_TOP_LEVEL_KEY, {}).get(CODEX_SERVER_NAME)
        if server_entry is None:
            raise ValueError(
                f"Post-write validation: {contract.idempotency_key} not found"
            )
        if server_entry.get("command") != resolved_command:
            raise ValueError(
                f"Post-write validation: command mismatch "
                f"(expected {resolved_command!r}, got {server_entry.get('command')!r})"
            )
        if server_entry.get("args") != expected_args:
            raise ValueError(
                f"Post-write validation: args mismatch "
                f"(expected {expected_args!r}, got {server_entry.get('args')!r})"
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
        raise CodexInstallError(
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
    "CodexInstallError",
    "build_contract",
    "format_config_snippet",
    "install",
    "render",
]
