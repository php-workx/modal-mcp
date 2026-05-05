"""CLI entrypoint for modal_mcp."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="modal-mcp",
        description="Modal MCP server shell.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Start the MCP server.")
    run_parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="Path to a .env file to load before starting the server.",
    )

    # setup subcommand
    setup_parser = subparsers.add_parser(
        "setup",
        help="Generate local MCP config and signing key, or print setup instructions.",
    )
    setup_parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help=(
            "Non-interactive: generate .env and a signing key without prompts. "
            "Existing keys are preserved (idempotent)."
        ),
    )
    setup_parser.add_argument(
        "--install",
        choices=["codex", "claude"],
        metavar="TARGET",
        default=None,
        help=(
            "Install the MCP config into the specified agent client. "
            "Supported values: codex, claude. "
            "Use with --dry-run to preview the change without writing."
        ),
    )
    setup_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Preview the change that --install would make, without writing any files."
        ),
    )
    setup_parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help=(
            "Absolute path to the .env file to write (with --yes) or embed in "
            "the installed config (with --install). "
            "Defaults to '.env' in the current directory."
        ),
    )
    setup_parser.add_argument(
        "--secrets-dir",
        metavar="DIR",
        default=None,
        help=(
            "Directory where the signing key is stored. "
            "Defaults to '.secrets' in the current directory."
        ),
    )
    setup_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Replace existing .env and signing key with fresh values. "
            "By default existing files are preserved (idempotent)."
        ),
    )

    # doctor subcommand
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run diagnostic checks on the installation.",
    )
    doctor_parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help=(
            "Path to a .env file to probe. Defaults to '.env' in the current directory."
        ),
    )

    # print-agent-config subcommand
    pac_parser = subparsers.add_parser(
        "print-agent-config",
        help="Print the agent client configuration block.",
    )
    pac_parser.add_argument(
        "--target",
        choices=["claude", "codex"],
        default="claude",
        help="Agent target to print config for (default: claude).",
    )
    pac_parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help=(
            "Absolute path to the .env file to embed in the config snippet. "
            "Required for --target codex."
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    """Start the MCP server, optionally loading an env file first."""
    env_file: str | None = getattr(args, "env_file", None)
    if env_file is not None:
        from pathlib import Path

        env_path = Path(env_file)
        if env_path.is_file():
            from dotenv import load_dotenv

            load_dotenv(str(env_path), override=False)
        else:
            print(f"warn: env file not found: {env_path}", file=sys.stderr)

    from modal_mcp.server import run

    run()
    return 0


def _cmd_setup(args: argparse.Namespace) -> int:
    """Generate setup files or print setup instructions."""
    from pathlib import Path

    yes: bool = getattr(args, "yes", False)
    install_target: str | None = getattr(args, "install", None)
    dry_run: bool = getattr(args, "dry_run", False)
    env_file_arg: str | None = getattr(args, "env_file", None)
    secrets_dir_arg: str | None = getattr(args, "secrets_dir", None)
    force: bool = getattr(args, "force", False)

    # ------------------------------------------------------------------
    # Resolve env-file path for both install targets
    # ------------------------------------------------------------------
    from modal_mcp.setup import DEFAULT_ENV_FILE

    resolved_env: Path
    if env_file_arg is not None:
        resolved_env = Path(env_file_arg).expanduser()
    else:
        resolved_env = Path(DEFAULT_ENV_FILE).expanduser().absolute()

    # ------------------------------------------------------------------
    # --install codex [--dry-run] [--env-file PATH]
    # ------------------------------------------------------------------
    if install_target == "codex":
        from modal_mcp.agent_config import CodexInstallError, install_codex_config

        # Codex embeds this path verbatim in its config so it MUST be
        # absolute — reject relative inputs with a clear error rather than
        # silently promoting them against the current CWD.
        if env_file_arg is not None and not resolved_env.is_absolute():
            print(
                f"error: --env-file must be an absolute path for"
                f" --install codex; got: {env_file_arg!r}",
                file=sys.stderr,
            )
            return 1
        if env_file_arg is None:
            resolved_env = resolved_env.absolute()

        try:
            install_codex_config(
                env_file=resolved_env,
                dry_run=dry_run,
                yes=yes,
            )
        except (CodexInstallError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    # ------------------------------------------------------------------
    # --install claude [--dry-run]
    # ------------------------------------------------------------------
    if install_target == "claude":
        from modal_mcp.agent_config import ClaudeInstallError, install_claude_config

        # Derive bind from env file or environment so the config URL matches
        # the running server.
        bind: str | None = None
        if resolved_env.exists():
            for line in resolved_env.read_text(encoding="utf-8").splitlines():
                if line.startswith("MODAL_MCP_HTTP_BIND="):
                    bind = line.split("=", 1)[1].strip()
                    break
        if bind is None:
            bind = os.environ.get("MODAL_MCP_HTTP_BIND")

        try:
            install_claude_config(
                bind=bind,
                dry_run=dry_run,
                yes=yes,
            )
        except (ClaudeInstallError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    # ------------------------------------------------------------------
    # --yes: non-interactive file generation
    # ------------------------------------------------------------------
    if yes:
        from modal_mcp.setup import DEFAULT_ENV_FILE, DEFAULT_SECRETS_DIR, run_setup
        from modal_mcp.setup_files import SetupFilesError

        resolved_env_file = (
            Path(env_file_arg).expanduser().absolute()
            if env_file_arg is not None
            else Path(DEFAULT_ENV_FILE).expanduser().absolute()
        )
        resolved_secrets_dir = (
            Path(secrets_dir_arg).expanduser().absolute()
            if secrets_dir_arg is not None
            else Path(DEFAULT_SECRETS_DIR).expanduser().absolute()
        )

        try:
            result = run_setup(
                env_file=resolved_env_file,
                secrets_dir=resolved_secrets_dir,
                force=force,
            )
        except SetupFilesError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        key_status = "created" if result.signing_key_created else "preserved"
        env_status = "created" if result.env_created else "preserved"
        print(f"Signing key  [{key_status}]: {result.signing_key_file}")
        print(f"Env file     [{env_status}]: {result.env_file}")
        print()
        print("Next steps:")
        print("  1. Add dedicated Modal token file paths to the env file.")
        print(f"  2. modal-mcp doctor --env-file {result.env_file}")
        print(f"  3. modal-mcp run --env-file {result.env_file}")
        return 0

    # Fallback: print setup instructions without writing any files.
    print("modal-mcp setup")
    print("-" * 40)
    print("Run 'modal-mcp print-agent-config' to see the configuration block,")
    print("then merge it into your agent client's config file.")
    print()
    print("To generate .env and a signing key automatically, run:")
    print("  modal-mcp setup --yes")
    print()
    print("Start the server with:")
    print("  modal-mcp run --env-file /absolute/path/to/.env")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Run partial diagnostic checks on the installation."""
    from pathlib import Path

    from modal_mcp.doctor import CheckStatus, run_doctor

    env_file_arg: str | None = getattr(args, "env_file", None)
    env_file: Path | None = (
        Path(env_file_arg).expanduser().absolute() if env_file_arg is not None else None
    )

    report = run_doctor(env_file=env_file)

    _PREFIX = {
        CheckStatus.OK: "ok  ",
        CheckStatus.WARN: "warn",
        CheckStatus.FAIL: "FAIL",
    }

    for item in report.items:
        line = f"{_PREFIX[item.status]} {item.message}"
        if item.status == CheckStatus.FAIL:
            print(line, file=sys.stderr)
        else:
            print(line)

    print()
    if report.has_failures:
        pass  # exit code already reflects failures via report.exit_code
    elif report.has_warnings:
        print("Partial ready: some items need attention (see warnings above).")
    else:
        print("All checks passed.")

    # Surface DiagnosticReport.exit_code directly so callers (CI, shell
    # pipelines) can distinguish: 0 = all OK, 3 = warnings only (partial
    # ready), 1 = hard failures present.
    return report.exit_code


def _cmd_print_agent_config(args: argparse.Namespace) -> int:
    """Print the agent configuration snippet for the requested target."""
    from pathlib import Path

    from modal_mcp.agent_config import print_agent_config

    target: str = getattr(args, "target", "claude") or "claude"
    env_file_arg: str | None = getattr(args, "env_file", None)
    env_file: Path | None = (
        Path(env_file_arg).expanduser().absolute() if env_file_arg is not None else None
    )
    print_agent_config(target, env_file=env_file)
    return 0


# ---------------------------------------------------------------------------
# Dispatch table  (None = no subcommand → preserve backward-compat behaviour)
# ---------------------------------------------------------------------------

_HANDLERS: dict[str | None, Callable[[argparse.Namespace], int]] = {
    None: _cmd_run,
    "run": _cmd_run,
    "setup": _cmd_setup,
    "doctor": _cmd_doctor,
    "print-agent-config": _cmd_print_agent_config,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI shell."""
    args = build_parser().parse_args(argv)
    handler = _HANDLERS[args.subcommand]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
