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
    "INSTALL_ERROR",
    "AgentTargetContract",
    "ClaudeInstallError",
    "build_contract",
    "format_config_snippet",
    "format_startup_command",
    "get_claude_config_dir",
    "get_claude_config_path",
    "install",
    "install_from_cli",
    "render",
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


def format_config_snippet(bind: str = CLAUDE_DEFAULT_BIND) -> str:
    """Render the Claude Desktop JSON config block as a string.

    Args:
        bind: The ``host:port`` the server listens on.  Defaults to
            ``CLAUDE_DEFAULT_BIND``.

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
    contract = build_contract(bind=bind)
    config_block: dict[str, object] = {
        CLAUDE_TOP_LEVEL_KEY: {
            CLAUDE_SERVER_NAME: {
                "type": CLAUDE_TRANSPORT,
                "url": contract.server_url,
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


# ---------------------------------------------------------------------------
# Install + render (absorbed from agent_config.py)
# ---------------------------------------------------------------------------


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


def _make_timestamp() -> str:
    """Return a compact UTC datetime string suitable for backup filenames."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically write text *content* to *path* via a sibling temp file."""
    encoded = content.encode()
    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_claude_")
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
    bind: str = CLAUDE_DEFAULT_BIND,
    file: TextIO | None = None,
) -> str:
    """Render the Claude Desktop JSON config snippet (with startup hint).

    When *file* is not None, prints the startup-command hint and snippet to
    *file* (matching the previous ``_print_claude_config`` behaviour).
    Always returns the snippet body.

    Args:
        env_file: Optional absolute path to the ``.env`` file used only for
            the startup-command hint.  Relative paths raise :class:`ValueError`.
        bind: The ``host:port`` the server listens on; embedded in the SSE URL.
            Defaults to :data:`CLAUDE_DEFAULT_BIND`.
        file: Optional file-like object to print to.

    Returns:
        The rendered JSON snippet.

    Raises:
        ValueError: If *env_file* is a non-absolute path.
    """
    if env_file is not None:
        startup_tokens = format_startup_command(env_file)
        startup_cmd = " ".join(startup_tokens)
    else:
        startup_cmd = f"modal-mcp run --env-file {_ENV_FILE_PLACEHOLDER}"

    snippet = format_config_snippet(bind=bind)
    if file is not None:
        print(
            "# Transport: HTTP/SSE (Claude Desktop connects to a running"
            " modal-mcp server)",
            file=file,
        )
        print("# 1. Start the server with an absolute --env-file path:", file=file)
        print(f"#    {startup_cmd}", file=file)
        print("# 2. Add this block to claude_desktop_config.json", file=file)
        print(snippet, end="", file=file)
    return snippet


def install(
    *,
    bind: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
    config_path: Path | None = None,
    file: TextIO | None = None,
    _timestamp: str | None = None,
) -> str:
    """Install the ``mcpServers.modal-mcp`` entry into Claude Desktop config.

    Supports dry-run previews, interactive confirmation, atomic backup,
    idempotent re-runs, and post-write validation.

    Args:
        bind: Optional ``host:port`` override for the SSE URL embedded in the
            config entry.  Defaults to :data:`CLAUDE_DEFAULT_BIND`.
        dry_run: When ``True``, preview the change and return ``"dry_run"``.
        yes: When ``True``, skip the interactive confirmation prompt.
        config_path: Override the default install target (platform-specific
            ``claude_desktop_config.json``).
        file: Output stream for status messages.  Defaults to ``sys.stdout``.
        _timestamp: Override the timestamp embedded in the backup suffix.

    Returns:
        ``"installed"``, ``"already_installed"``, ``"declined"``, or
        ``"dry_run"``.

    Raises:
        ClaudeInstallError: If a safety check fails.
    """
    out = sys.stdout if file is None else file

    contract = build_contract(bind=bind) if bind is not None else build_contract()
    sse_url = contract.server_url  # always non-None for SSE transport

    new_entry: JsonTable = {"type": CLAUDE_TRANSPORT, "url": sse_url}
    snippet = (
        json.dumps({CLAUDE_TOP_LEVEL_KEY: {CLAUDE_SERVER_NAME: new_entry}}, indent=2)
        + "\n"
    )

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
    # 2.  Dry-run
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
        raise ClaudeInstallError(
            f"Config directory {resolved_config.parent} does not exist. "
            "Is Claude Desktop installed and has it been launched at least once?"
        )

    if resolved_config.is_symlink():
        raise ClaudeInstallError(
            f"Config file {resolved_config} is a symlink. "
            "Refusing to write through a symlink."
        )

    if resolved_config.exists() and not resolved_config.is_file():
        raise ClaudeInstallError(
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
            raise ClaudeInstallError(
                f"Cannot read config file {resolved_config}: {exc}"
            ) from exc

        try:
            existing_data = json.loads(existing_content)
        except json.JSONDecodeError as exc:
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
    mcp_servers = cast(JsonTable, mcp_servers_value)

    if CLAUDE_SERVER_NAME in mcp_servers:
        existing_entry_value = mcp_servers[CLAUDE_SERVER_NAME]
        if not isinstance(existing_entry_value, dict):
            raise ClaudeInstallError(
                f"Config file {resolved_config}: {contract.idempotency_key} "
                "is present but is not a JSON object."
            )
        existing_entry = cast(JsonTable, existing_entry_value)
        if (
            existing_entry.get("type") == CLAUDE_TRANSPORT
            and existing_entry.get("url") == sse_url
        ):
            print(
                f"Already installed: {contract.idempotency_key} in {resolved_config}",
                file=out,
            )
            return "already_installed"
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

    new_data: JsonTable = {**existing_data}
    if CLAUDE_TOP_LEVEL_KEY not in new_data:
        new_data[CLAUDE_TOP_LEVEL_KEY] = {}
    new_data[CLAUDE_TOP_LEVEL_KEY][CLAUDE_SERVER_NAME] = new_entry
    new_content = json.dumps(new_data, indent=2) + "\n"

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
        validated = json.loads(resolved_config.read_text(encoding="utf-8"))
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


#: Concrete exception class raised by :func:`install` on safety-check failure.
#:
#: Exposed for the CLI layer so it can catch the install error without
#: importing the concrete class by name (which couples the CLI to per-target
#: module internals).  Every agent target module must expose this attribute.
INSTALL_ERROR: Final[type[Exception]] = ClaudeInstallError


def install_from_cli(args: argparse.Namespace) -> int:
    """Run the Claude Desktop install from a parsed CLI ``args`` namespace.

    Returns an exit code (0 on success, 1 on install failure).  Handles the
    Claude-specific argument parsing (reading the SSE bind from the env file
    or environment) and exception mapping so that ``cli/setup.py`` does not
    need any per-target branching.
    """
    from modal_mcp.setup import DEFAULT_ENV_FILE

    env_file_arg: str | None = getattr(args, "env_file", None)
    dry_run: bool = getattr(args, "dry_run", False)
    yes: bool = getattr(args, "yes", False)

    if env_file_arg is not None:
        resolved_env = Path(env_file_arg).expanduser().absolute()
    else:
        resolved_env = Path(DEFAULT_ENV_FILE).expanduser().absolute()

    bind: str | None = None
    if resolved_env.exists():
        for line in resolved_env.read_text(encoding="utf-8").splitlines():
            if line.startswith("MODAL_MCP_HTTP_BIND="):
                bind = line.split("=", 1)[1].strip()
                break
    if bind is None:
        bind = os.environ.get("MODAL_MCP_HTTP_BIND")

    try:
        install(bind=bind, dry_run=dry_run, yes=yes)
    except (ValueError, INSTALL_ERROR) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
