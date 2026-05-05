"""Claude Desktop agent target contract for Modal MCP install operations.

This module specifies *exactly* how ``modal-mcp`` registers itself in the
Claude Desktop configuration.  No file is written here; the contract is a
frozen dataclass that install and print code can read, and that tests can assert
before any write reaches the user's filesystem.

Install contract summary
------------------------

.. list-table::

   * - Config path
     - Platform-specific ``claude_desktop_config.json``; use
       :func:`get_claude_config_path` at install time.
       Representative macOS path:
       ``~/Library/Application Support/Claude/claude_desktop_config.json``
   * - Format
     - JSON
   * - Transport
     - SSE — the ``modal-mcp`` server must be running before Claude Desktop
       connects.  Claude connects to ``http://127.0.0.1:8765/mcp/sse``.
   * - Top-level key
     - ``mcpServers``
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
     - Not applicable.  Claude Desktop does not launch ``modal-mcp``; the
       server is started separately via
       ``modal-mcp run --env-file <absolute-path>``.
   * - Refusal conditions
     - Eight conditions listed in :data:`CLAUDE_CONTRACT`.
   * - Parse validation
     - ``json.loads`` round-trip; assert ``mcpServers.modal-mcp`` is present
       with the expected ``type`` and ``url`` values.

Generated config block
----------------------

The installer merges the following entry under the ``mcpServers`` key::

    "modal-mcp": {
        "type": "sse",
        "url": "http://127.0.0.1:8765/mcp/sse"
    }

If ``mcpServers`` does not yet exist it is created.  If an identically-keyed
entry already contains the correct ``type`` and ``url`` the install is a no-op.

SSE transport schema reference
--------------------------------

The ``{type: "sse", url: "..."}`` shape inside ``mcpServers`` is the format
documented for Claude Desktop's remote/HTTP MCP server entries and was verified
against the Claude Desktop configuration guide at:
https://modelcontextprotocol.io/quickstart/user (section "Connecting to a
remote server").  The ``streamable-http`` transport literal (also defined in
:data:`McpTransport`) is the newer streaming variant; Claude Desktop >= 0.9
supports both.  Install code should prefer ``sse`` for broadest compatibility
and can fall back to ``streamable-http`` if Claude Desktop reports the SSE
endpoint is unsupported.

Platform-specific paths
-----------------------

======= =====================================================================
macOS   ``~/Library/Application Support/Claude/claude_desktop_config.json``
Windows ``%APPDATA%\\Claude\\claude_desktop_config.json``
Linux   ``${XDG_CONFIG_HOME:-~/.config}/Claude/claude_desktop_config.json``
======= =====================================================================

The contract stores the macOS path as the canonical representative value in
:attr:`~AgentTargetContract.representative_config_path`.
Install code **must** call :func:`get_claude_config_path` to get the correct
runtime path.  On macOS, ``get_claude_config_path()`` returns exactly
``contract.representative_config_path.expanduser()``.

.. note::

   There is no official Claude Desktop release for Linux at the time of
   writing.  The Linux path follows the layout used by community-packaged
   builds (e.g. AUR ``claude-desktop-bin``) and the unofficial AppImage
   distributed by the community.  :func:`get_claude_config_dir` returns the
   Linux path so that early adopters on unofficial builds can use the
   installer; the path has no guarantee of correctness on all Linux
   distributions.

Startup command / env-file
---------------------------

Claude Desktop does not launch ``modal-mcp`` directly; the user starts the
server independently before Claude connects.  The recommended startup command
uses an *absolute* ``--env-file`` path so the server finds its settings
regardless of the shell's working directory::

    modal-mcp run --env-file /absolute/path/to/.env

Use :func:`format_startup_command` to render this snippet with a concrete
path.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Final

from modal_mcp.agent_targets.contract import AgentTargetContract

# ---------------------------------------------------------------------------
# Public re-export so callers can import AgentTargetContract from claude.py
# ---------------------------------------------------------------------------

__all__ = [
    "CLAUDE_AGENT_NAME",
    "CLAUDE_BACKUP_SUFFIX_TEMPLATE",
    "CLAUDE_CONFIG_FILENAME",
    "CLAUDE_CONFIG_FORMAT",
    "CLAUDE_CONTRACT",
    "CLAUDE_DEFAULT_BIND",
    "CLAUDE_ENV_FILE_FLAG",
    "CLAUDE_IDEMPOTENCY_KEY",
    "CLAUDE_MCP_PATH",
    "CLAUDE_MCP_SSE_PATH",
    "CLAUDE_SERVER_NAME",
    "CLAUDE_SSE_URL",
    "CLAUDE_TOP_LEVEL_KEY",
    "CLAUDE_TRANSPORT",
    "AgentTargetContract",
    "build_contract",
    "format_config_snippet",
    "format_startup_command",
    "get_claude_config_dir",
    "get_claude_config_path",
]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Human-readable agent identifier.
CLAUDE_AGENT_NAME: Final[str] = "claude_desktop"

#: Filename of the Claude Desktop configuration file.
CLAUDE_CONFIG_FILENAME: Final[str] = "claude_desktop_config.json"

#: File format written and read by the installer.
CLAUDE_CONFIG_FORMAT: Final = "json"

#: MCP transport type.  ``modal-mcp`` is an HTTP/ASGI server; Claude Desktop
#: connects via Server-Sent Events (SSE).
CLAUDE_TRANSPORT: Final[str] = "sse"

#: Registration name for the server inside ``mcpServers``.
CLAUDE_SERVER_NAME: Final[str] = "modal-mcp"

#: Top-level JSON key under which MCP server registrations are merged.
CLAUDE_TOP_LEVEL_KEY: Final[str] = "mcpServers"

#: Dotted config path used for idempotency checks.
CLAUDE_IDEMPOTENCY_KEY: Final[str] = "mcpServers.modal-mcp"

#: Default host:port used by ``modal-mcp``
#: (matches :class:`~modal_mcp.config.Settings`).
CLAUDE_DEFAULT_BIND: Final[str] = "127.0.0.1:8765"

#: Base MCP path on the local server.
CLAUDE_MCP_PATH: Final[str] = "/mcp"

#: SSE path on the local server.
CLAUDE_MCP_SSE_PATH: Final[str] = "/mcp/sse"

#: Default SSE URL used in the generated config entry.
CLAUDE_SSE_URL: Final[str] = f"http://{CLAUDE_DEFAULT_BIND}{CLAUDE_MCP_SSE_PATH}"

#: CLI flag used to pass an explicit env-file path to ``modal-mcp run``.
CLAUDE_ENV_FILE_FLAG: Final[str] = "--env-file"

#: Backup filename suffix template.  ``{timestamp}`` is substituted with a
#: filesystem-safe compact UTC datetime at write time, e.g.
#: ``.bak.20260419T103000``.  The compact format (``YYYYMMDDTHHmmss``) avoids
#: the ``:`` character that is illegal on Windows NTFS/FAT32 filesystems.
CLAUDE_BACKUP_SUFFIX_TEMPLATE: Final[str] = ".bak.{timestamp}"

# ---------------------------------------------------------------------------
# Platform-specific config path helpers
# ---------------------------------------------------------------------------


def get_claude_config_dir() -> Path | None:
    """Return the platform-specific Claude Desktop config directory.

    Returns ``None`` when the platform is not supported (i.e. not macOS,
    Windows, or Linux).  The directory may not exist even on supported
    platforms if Claude Desktop has never been launched.

    .. note::

        The Linux path (``${XDG_CONFIG_HOME:-~/.config}/Claude/``) is not
        based on an official Claude Desktop Linux release — no such release
        exists at the time of writing.  The path matches the layout used by
        community-packaged builds (e.g. the AUR ``claude-desktop-bin`` package
        and unofficial AppImage distributions).  Install code should treat a
        non-``None`` Linux return value as *best-effort* and must still verify
        that the config directory actually exists before writing.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Claude"
    if sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        return base / "Claude"
    return None


def get_claude_config_path() -> Path | None:
    """Return the runtime path to ``claude_desktop_config.json``.

    The returned path is absolute and expanded for the current platform.
    Returns ``None`` on unsupported platforms.  The file may not yet exist;
    the installer is responsible for creating it when absent.

    .. important::

        Install code **must** call this function to resolve the write target.
        Do **not** read :attr:`~AgentTargetContract.representative_config_path`
        from the contract directly; that field stores the macOS path as a
        representative value and will point to a non-existent location on
        Windows and Linux.
    """
    config_dir = get_claude_config_dir()
    if config_dir is None:
        return None
    return config_dir / CLAUDE_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Startup command helper
# ---------------------------------------------------------------------------

#: Startup command template tokens.  ``{env_file}`` must be replaced with the
#: *absolute* path to the ``.env`` file before the snippet is shown or stored.
_STARTUP_COMMAND_TEMPLATE: Final[tuple[str, ...]] = (
    "modal-mcp",
    "run",
    CLAUDE_ENV_FILE_FLAG,
    "{env_file}",
)


def format_startup_command(env_file: str | Path) -> tuple[str, ...]:
    """Return the startup command with ``{env_file}`` substituted.

    Args:
        env_file: *Absolute* path to the ``.env`` file.

    Returns:
        A tuple of command tokens ready for display or use with ``subprocess``.

    Raises:
        ValueError: If ``env_file`` is not an absolute path.
    """
    env_file_path = Path(env_file)
    if not env_file_path.is_absolute():
        msg = f"env_file must be an absolute path; got: {env_file!r}"
        raise ValueError(msg)
    return tuple(
        token.format(env_file=str(env_file_path)) for token in _STARTUP_COMMAND_TEMPLATE
    )


# ---------------------------------------------------------------------------
# Config snippet renderer
# ---------------------------------------------------------------------------

#: Absolute env-file placeholder used when no path is supplied to
#: :func:`format_config_snippet`.  The leading ``/`` makes the placeholder
#: recognisably absolute so users understand they must supply a real path.
_ENV_FILE_PLACEHOLDER: Final[str] = "/absolute/path/to/.env"


def format_config_snippet() -> str:
    """Render the Claude Desktop JSON config block as a string.

    The returned string is a ready-to-paste JSON object that should be merged
    into ``claude_desktop_config.json`` under the existing top-level keys.

    Transport is SSE — Claude Desktop connects to a *running* ``modal-mcp``
    server; it does not launch ``modal-mcp`` directly.  Start the server with
    an absolute ``--env-file`` path before Claude Desktop connects::

        modal-mcp run --env-file /absolute/path/to/.env

    This function does not write any files and does not read secrets from the
    environment or filesystem.

    Returns:
        A JSON snippet string containing the ``mcpServers`` entry with the
        ``type`` and ``url`` fields populated.
    """
    config_block: dict[str, object] = {
        CLAUDE_TOP_LEVEL_KEY: {
            CLAUDE_SERVER_NAME: {
                "type": CLAUDE_TRANSPORT,
                "url": CLAUDE_SSE_URL,
            }
        }
    }
    return json.dumps(config_block, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------


def build_contract(bind: str = CLAUDE_DEFAULT_BIND) -> AgentTargetContract:
    """Construct the canonical Claude Desktop install contract.

    Args:
        bind: The ``host:port`` the server listens on.  Defaults to
            ``CLAUDE_DEFAULT_BIND`` (``"127.0.0.1:8765"``).

    Returns:
        A frozen :class:`AgentTargetContract` instance.

    .. important::

        Install code MUST pass the bind address derived from
        :class:`~modal_mcp.config.Settings` so that ``server_url``,
        ``parse_validation_strategy``, and ``dry_run_description`` all reflect
        the address the server is actually listening on.  Do NOT use the
        module-level :data:`CLAUDE_CONTRACT` singleton when the user's
        ``Settings.bind`` differs from :data:`CLAUDE_DEFAULT_BIND`; call this
        function with ``bind=settings.bind`` instead.
    """
    sse_url = f"http://{bind}{CLAUDE_MCP_SSE_PATH}"
    return AgentTargetContract(
        # --- identity ---
        agent_name=CLAUDE_AGENT_NAME,
        # --- config location ---
        # The macOS path is stored as the contract's *representative* value so
        # that tests can inspect it without a platform dependency.
        # IMPORTANT: install code must NEVER read representative_config_path
        # directly to determine where to write the config.  Always call
        # get_claude_config_path() at install time, which returns the correct
        # expanded path for the running OS.  On macOS,
        # get_claude_config_path() == representative_config_path.expanduser().
        representative_config_path=Path(
            "~/Library/Application Support/Claude/claude_desktop_config.json"
        ),
        config_format=CLAUDE_CONFIG_FORMAT,
        # --- MCP transport ---
        # modal-mcp is an HTTP/ASGI server.  Claude Desktop connects via SSE.
        mcp_transport="sse",
        server_name=CLAUDE_SERVER_NAME,
        top_level_key=CLAUDE_TOP_LEVEL_KEY,
        # --- transport-specific ---
        server_url=sse_url,
        server_command=None,  # not used for SSE transport
        server_args_template=(),  # not used for SSE transport
        # --- cwd / env-file ---
        # SSE transport: Claude Desktop never invokes modal-mcp directly.
        # The server is started separately with an explicit --env-file flag.
        # The Claude Desktop config entry is therefore self-contained.
        env_file_strategy="not_applicable",
        supports_cwd_config=False,
        # --- install mechanics ---
        # Backup: claude_desktop_config.json.bak.20260419T103000
        # Compact format avoids ':' which is illegal on Windows NTFS/FAT32.
        backup_suffix_template=CLAUDE_BACKUP_SUFFIX_TEMPLATE,
        refusal_conditions=(
            "config directory does not exist "
            "(Claude Desktop not installed or never launched)",
            "config file is a symlink (refused to write through symlink)",
            "config file exists but cannot be parsed as valid JSON",
            "merged config fails JSON round-trip validation",
            "config file or directory is not readable or writable "
            "(permission denied — check file ownership after user switch)",
            "disk full or out of space (ENOSPC) during atomic temp-file write",
            "config path exists but is not a regular file "
            "(directory, symlink, FIFO, socket, or device node)",
            "mcpServers.modal-mcp already exists with an incompatible transport "
            "or url (e.g. a stdio entry where an SSE entry is expected)",
        ),
        # Validation: after writing, reload with json.loads and assert the
        # expected structure is present.  The original file is restored from
        # the backup if validation fails.
        parse_validation_strategy=(
            "after writing, reload the file with json.loads; "
            f"assert the result is a dict that contains mcpServers.modal-mcp "
            f"with type='sse' and url='{sse_url}'; "
            "restore backup and raise if validation fails"
        ),
        dry_run_description=(
            f"would add mcpServers.modal-mcp "
            f"(type: sse, url: {sse_url}) "
            "to claude_desktop_config.json"
        ),
        idempotency_key=CLAUDE_IDEMPOTENCY_KEY,
    )


#: Default-bind contract for display, testing, and documentation purposes.
#:
#: .. warning::
#:
#:     **Install code must not import this singleton.**  It is frozen with
#:     :data:`CLAUDE_DEFAULT_BIND` (``"127.0.0.1:8765"``).  If the user
#:     configures a different bind address (e.g. via ``Settings.bind``), the
#:     ``server_url``, ``parse_validation_strategy``, and ``dry_run_description``
#:     embedded in this singleton will all reference a URL the server is **not**
#:     listening on.  Install code must always call
#:     ``build_contract(bind=settings.bind)`` and use the returned instance.
CLAUDE_CONTRACT: Final[AgentTargetContract] = build_contract()
