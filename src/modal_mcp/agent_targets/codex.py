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
       ``args = ["run", "--env-file", "<absolute-path>"]`` so that Codex can
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
    args = ["run", "--env-file", "/absolute/path/to/.env"]

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

from pathlib import Path
from typing import Final

from modal_mcp.agent_targets.contract import AgentTargetContract

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
CODEX_SERVER_ARGS_TEMPLATE: Final[tuple[str, ...]] = ("run", "--env-file", "{env_file}")

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
            f"with command='{command}' and args starting with 'run'; "
            "restore backup and raise if validation fails"
        ),
        dry_run_description=(
            f"would add [mcp_servers.modal-mcp] "
            f"(command: {command}, args: run --env-file <absolute-path>) "
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
        return s.replace("\\", "\\\\").replace('"', '"')

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
    "build_contract",
    "format_config_snippet",
]
