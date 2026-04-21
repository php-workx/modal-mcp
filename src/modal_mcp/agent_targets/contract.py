"""Shared contract types for Modal MCP agent install targets.

This module is the single source of truth for the :class:`AgentTargetContract`
dataclass and its associated :data:`ConfigFormat`, :data:`McpTransport`, and
:data:`EnvFileStrategy` type aliases.  Every agent target module
(``claude.py``, ``codex.py``, and any future targets) imports from here rather
than from each other, preventing circular-import dependency chains.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ConfigFormat = Literal["yaml", "json", "toml"]
"""Serialization format of the agent config file."""

McpTransport = Literal["http", "stdio", "sse", "streamable-http"]
"""MCP transport type used when the agent connects to the server."""

EnvFileStrategy = Literal["absolute_path_flag", "cwd", "not_applicable"]
"""How the env-file path is supplied to the launched server command.

``absolute_path_flag``
    An ``--env-file <absolute-path>`` flag is appended to the launch args.
``cwd``
    The target config schema supports an explicit working-directory field that
    is set to the project directory; the server process finds ``.env`` via cwd.
``not_applicable``
    HTTP/SSE transport is used; the agent does not launch ``modal-mcp``, so no
    env-file injection is required in the agent config.
"""


@dataclass(frozen=True)
class AgentTargetContract:
    """Structured install contract for a coding-agent MCP target.

    Every field must be populated with a concrete value before install code is
    written.  Tests assert each field so that a reviewer can audit the exact
    install behaviour without running it.

    The contract covers:

    * where to write (``representative_config_path``, ``config_format``),
    * how the agent connects (``mcp_transport``, ``server_url`` / ``server_command``),
    * how to merge safely (``top_level_key``, ``server_name``, ``idempotency_key``),
    * how to back up (``backup_suffix_template``),
    * when to refuse (``refusal_conditions``),
    * how to validate the result (``parse_validation_strategy``),
    * what to show in dry-run mode (``dry_run_description``), and
    * cwd / env-file launch behaviour (``env_file_strategy``, ``supports_cwd_config``).

    .. important::

        ``representative_config_path`` is intentionally named *representative*: on
        targets where the config path is platform-specific (e.g. Claude Desktop),
        this field stores a canonical reference path for documentation and testing
        purposes only.  Install code must **never** read
        ``representative_config_path`` directly to determine where to write; it
        must use the platform-aware helper provided by the target module (e.g.
        ``get_claude_config_path()``).  On targets where the path is
        platform-agnostic (e.g. Codex), the representative path equals the actual
        install path after :py:meth:`~pathlib.Path.expanduser`.
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    agent_name: str
    """Human-readable agent identifier, e.g. ``'codex'``."""

    # ------------------------------------------------------------------
    # Config location
    # ------------------------------------------------------------------

    representative_config_path: Path
    """Representative (unexpanded ``~``) path to the agent config file.

    For targets where the config path is **platform-agnostic** (e.g. Codex's
    ``~/.codex/config.toml``), this equals the actual install path after
    :py:meth:`~pathlib.Path.expanduser`.

    For targets where the config path is **platform-specific** (e.g. Claude
    Desktop), this stores the macOS path as a canonical reference for
    documentation and tests.  Install code on other platforms **must** call the
    target module's ``get_<agent>_config_path()`` helper instead of reading
    this field directly; doing so on Windows or Linux will silently point at a
    non-existent macOS path.
    """

    config_format: ConfigFormat
    """Serialization format of the agent config file."""

    # ------------------------------------------------------------------
    # MCP transport
    # ------------------------------------------------------------------

    mcp_transport: McpTransport
    """MCP transport type used when the agent connects to this server."""

    server_name: str
    """Key used to identify this server within the agent's MCP servers mapping."""

    top_level_key: str
    """Top-level config key under which MCP server entries are nested."""

    # ------------------------------------------------------------------
    # Transport-specific settings
    # ------------------------------------------------------------------

    server_url: str | None
    """HTTP endpoint URL.  Populated for ``http``/``sse`` transport; ``None`` for
    stdio transport."""

    server_command: str | None
    """Executable name.  Populated for ``stdio`` transport; ``None`` for HTTP."""

    server_args_template: tuple[str, ...]
    """Argument list template for stdio launch.  ``'{env_file}'`` may appear as a
    placeholder that is replaced with an absolute path at install time."""

    # ------------------------------------------------------------------
    # Cwd / env-file handling
    # ------------------------------------------------------------------

    env_file_strategy: EnvFileStrategy
    """How the env-file path is passed to the launched ``modal-mcp`` process."""

    supports_cwd_config: bool
    """``True`` when the target config schema allows setting a ``cwd`` for command
    launch."""

    # ------------------------------------------------------------------
    # Install mechanics
    # ------------------------------------------------------------------

    backup_suffix_template: str
    """Suffix appended to the config filename for the timestamped backup.
    ``'{timestamp}'`` is substituted with a filesystem-safe compact UTC datetime
    string at write time, e.g. ``'.bak.20260419T103000'``.  The compact
    ``YYYYMMDDTHHmmss`` format avoids the ``:`` character, which is illegal in
    filenames on Windows NTFS/FAT32."""

    refusal_conditions: tuple[str, ...]
    """Human-readable descriptions of conditions that must abort the install.
    At least three distinct conditions must be listed."""

    parse_validation_strategy: str
    """Description of how to verify the written config is structurally valid
    before the backup is removed and the write is considered permanent."""

    dry_run_description: str
    """Short description of the change displayed in dry-run mode."""

    idempotency_key: str
    """Dotted config path used to detect an existing registration so that reruns
    are a no-op when the entry already matches, e.g. ``'mcpServers.modal-mcp'``."""


__all__ = [
    "AgentTargetContract",
    "ConfigFormat",
    "EnvFileStrategy",
    "McpTransport",
]
