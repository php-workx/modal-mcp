"""Claude Code agent target contract for Modal MCP install operations.

This module specifies exactly how ``modal-mcp`` registers itself in the
Claude Code MCP configuration.  Instead of writing JSON files directly, it
delegates all registration operations to the ``claude mcp add-json`` CLI
subprocess.

Install contract summary
------------------------

.. list-table::

   * - Config path
     - ``~/.claude.json``
   * - Format
     - JSON (managed by ``claude`` CLI)
   * - Transport
     - stdio — Claude Code launches ``modal-mcp`` directly as a subprocess.
   * - Top-level key
     - ``mcpServers``
   * - Server name / merge key
     - ``modal-mcp``
   * - Idempotency key
     - ``mcpServers.modal-mcp``
   * - Cwd / env-file strategy
     - ``absolute_path_flag``: the ``--env-file <absolute-path>`` flag is
       embedded in the ``args`` list.
   * - Refusal conditions
     - Three conditions listed in :data:`CONTRACT`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import IO, Final, Literal, TextIO

from modal_mcp.agent_targets.contract import AgentTargetContract

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

CLAUDE_CODE_SERVER_COMMAND: Final[str] = "modal-mcp"
CLAUDE_CODE_SERVER_NAME: Final[str] = "modal-mcp"
CLAUDE_CODE_TOP_LEVEL_KEY: Final[str] = "mcpServers"
CLAUDE_CODE_TRANSPORT: Final[str] = "stdio"
CLAUDE_CODE_DEFAULT_SCOPE: Final[str] = "user"
CLAUDE_CODE_SERVER_ARGS_TEMPLATE: Final[tuple[str, ...]] = (
    "stdio",
    "--env-file",
    "{env_file}",
)

Scope = Literal["user", "project", "local"]

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ClaudeCodeInstallError(Exception):
    """Raised when the Claude Code install fails."""


INSTALL_ERROR = ClaudeCodeInstallError

# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

CONTRACT = AgentTargetContract(
    agent_name="claude_code",
    representative_config_path=Path("~/.claude.json"),
    config_format="json",
    mcp_transport="stdio",
    server_name="modal-mcp",
    top_level_key="mcpServers",
    server_url=None,
    server_command="modal-mcp",
    server_args_template=("stdio", "--env-file", "{env_file}"),
    env_file_strategy="absolute_path_flag",
    supports_cwd_config=False,
    backup_suffix_template="not_applicable_managed_by_claude_cli",
    refusal_conditions=(
        "claude CLI not found on PATH",
        "env_file is not an absolute path",
        "subprocess call to claude mcp add-json exits non-zero",
    ),
    parse_validation_strategy=("run 'claude mcp get modal-mcp' and check exit code 0"),
    dry_run_description=(
        "add mcpServers.modal-mcp entry via 'claude mcp add-json'"
        " (stdio transport, --scope user)"
    ),
    idempotency_key="mcpServers.modal-mcp",
)

# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------


def render(
    *,
    env_file: str,
    scope: Scope = CLAUDE_CODE_DEFAULT_SCOPE,
    file: IO[str] | None = None,
) -> None:
    """Print the ``claude mcp add-json`` command that registers modal-mcp.

    Args:
        env_file: Absolute path to the ``.env`` file.
        scope: Claude Code scope — ``"user"``, ``"project"``, or ``"local"``.
            Defaults to ``"user"``.
        file: Output file-like object.  Defaults to :data:`sys.stdout`.

    Raises:
        ValueError: If *env_file* is not an absolute path.
    """
    env_path = Path(env_file)
    if not env_path.is_absolute():
        raise ValueError(f"env_file must be an absolute path; got: {env_file}")

    env_path_str = str(env_path)
    args_list = [
        arg.format(env_file=env_path_str) if "{env_file}" in arg else arg
        for arg in CLAUDE_CODE_SERVER_ARGS_TEMPLATE
    ]
    json_entry = {
        "type": CLAUDE_CODE_TRANSPORT,
        "command": CLAUDE_CODE_SERVER_COMMAND,
        "args": args_list,
    }
    json_str = json.dumps(json_entry, separators=(",", ":"))
    command = (
        f"claude mcp add-json {CLAUDE_CODE_SERVER_NAME} '{json_str}' --scope {scope}"
    )

    out = file if file is not None else sys.stdout
    print(
        "# Run this command to register modal-mcp with Claude Code:",
        file=out,
    )
    print(command, file=out)


# ---------------------------------------------------------------------------
# install()
# ---------------------------------------------------------------------------


def install(
    *,
    env_file: str,
    dry_run: bool = False,
    yes: bool = False,
    scope: Scope = CLAUDE_CODE_DEFAULT_SCOPE,
    _claude_json_path: Path | None = None,
    file: TextIO | None = None,
) -> str:
    """Install modal-mcp into Claude Code via ``claude mcp add-json``.

    Args:
        env_file: Absolute path to the ``.env`` file.
        dry_run: If ``True``, print what would be done and return ``"dry_run"``
            without making any changes.
        yes: If ``True``, skip the interactive confirmation prompt.
        scope: Claude Code scope — ``"user"``, ``"project"``, or ``"local"``.
        _claude_json_path: Override the path to ``~/.claude.json`` for testing.
        file: Output stream for status messages.  Defaults to ``sys.stdout``.

    Returns:
        One of ``"installed"``, ``"already_installed"``, ``"dry_run"``,
        or ``"declined"``.

    Raises:
        ValueError: If *env_file* is not an absolute path.
        ClaudeCodeInstallError: If ``claude`` is not on PATH or the subprocess
            exits non-zero.
    """
    out = sys.stdout if file is None else file

    # 1. Validate env_file is absolute.
    env_path = Path(env_file)
    if not env_path.is_absolute():
        raise ValueError(f"env_file must be an absolute path; got: {env_file}")
    env_path_str = str(env_path)

    # 2. Check claude CLI is available.
    if shutil.which("claude") is None:
        raise ClaudeCodeInstallError(
            "claude CLI not found on PATH. Install from https://claude.ai/download"
        )

    # 3. Resolve claude.json path.
    # Only check idempotency via file read for user scope (or test override).
    # For project/local scope we cannot reliably locate the right config file.
    claude_json_path: Path | None
    if _claude_json_path is not None:
        claude_json_path = _claude_json_path
    elif scope == "user":
        claude_json_path = Path("~/.claude.json").expanduser()
    else:
        claude_json_path = None

    # 4. Build args list from template.
    args_list = [
        arg.format(env_file=env_path_str) if "{env_file}" in arg else arg
        for arg in CLAUDE_CODE_SERVER_ARGS_TEMPLATE
    ]

    # 5. Read existing entry for idempotency (user scope / test override only).
    needs_remove = False
    expected_entry = {
        "command": CLAUDE_CODE_SERVER_COMMAND,
        "args": args_list,
    }

    if claude_json_path is not None and claude_json_path.exists():
        try:
            data = json.loads(claude_json_path.read_text(encoding="utf-8"))
            mcp_servers = data.get(CLAUDE_CODE_TOP_LEVEL_KEY, {})
            existing_entry = mcp_servers.get(CLAUDE_CODE_SERVER_NAME)
        except (json.JSONDecodeError, AttributeError):
            existing_entry = None

        if existing_entry is not None:
            if existing_entry == expected_entry:
                return "already_installed"
            else:
                needs_remove = True

    # 6. Build json_entry for subprocess call.
    json_entry = {
        "type": CLAUDE_CODE_TRANSPORT,
        "command": CLAUDE_CODE_SERVER_COMMAND,
        "args": args_list,
    }

    # 7. Dry run.
    if dry_run:
        json_str = json.dumps(json_entry, separators=(",", ":"))
        command = (
            f"claude mcp add-json {CLAUDE_CODE_SERVER_NAME}"
            f" '{json_str}' --scope {scope}"
        )
        display_path = (
            claude_json_path if claude_json_path is not None else "~/.claude.json"
        )
        print(f"Target: {display_path}", file=out)
        print(f"Change: {CONTRACT.dry_run_description}", file=out)
        print(file=out)
        print("Would run:", file=out)
        print(command, file=out)
        return "dry_run"

    # 8. Prompt if not yes.
    if not yes:
        try:
            answer = input("Install modal-mcp entry? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            return "declined"

    # 9. Remove existing entry if needed.
    if needs_remove:
        try:
            subprocess.run(
                [
                    "claude",
                    "mcp",
                    "remove",
                    CLAUDE_CODE_SERVER_NAME,
                    "--scope",
                    scope,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ClaudeCodeInstallError(
                f"claude mcp remove failed: {exc.stderr}"
            ) from exc

    # 10. Add the new entry.
    try:
        subprocess.run(
            [
                "claude",
                "mcp",
                "add-json",
                CLAUDE_CODE_SERVER_NAME,
                json.dumps(json_entry, separators=(",", ":")),
                "--scope",
                scope,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ClaudeCodeInstallError(
            f"claude mcp add-json failed: {exc.stderr}"
        ) from exc

    # 11. Return "installed".
    print(
        f"Installed: {CONTRACT.idempotency_key} registered with"
        f" Claude Code (scope={scope})",
        file=out,
    )
    return "installed"


# ---------------------------------------------------------------------------
# install_from_cli()
# ---------------------------------------------------------------------------


def install_from_cli(
    args: argparse.Namespace,
    *,
    _claude_json_path: Path | None = None,
) -> int:
    """Entry point called by the CLI layer for ``--install claude_code``.

    Args:
        args: Parsed CLI arguments (``env_file``, ``dry_run``, ``yes``,
            ``scope`` attributes are read via :func:`getattr` with defaults).
        _claude_json_path: Override path for testing.

    Returns:
        Exit code: ``0`` on success, ``1`` on error.
    """
    from modal_mcp.setup import DEFAULT_ENV_FILE

    env_file_arg: str | None = getattr(args, "env_file", None)
    dry_run: bool = getattr(args, "dry_run", False)
    yes: bool = getattr(args, "yes", False)
    scope: str = getattr(args, "scope", CLAUDE_CODE_DEFAULT_SCOPE)

    if env_file_arg is not None:
        resolved_env = Path(env_file_arg).expanduser().absolute()
    else:
        resolved_env = Path(DEFAULT_ENV_FILE).expanduser().absolute()

    try:
        install(
            env_file=str(resolved_env),
            dry_run=dry_run,
            yes=yes,
            scope=scope,
            _claude_json_path=_claude_json_path,
        )
    except (ValueError, INSTALL_ERROR) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "CONTRACT",
    "INSTALL_ERROR",
    "ClaudeCodeInstallError",
    "install",
    "install_from_cli",
    "render",
]
