"""``modal-mcp setup`` — generate config + signing key, or install a target."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import ClassVar


class SetupCommand:
    """Generate local MCP config and signing key, or print setup instructions."""

    name: ClassVar[str] = "setup"

    @classmethod
    def register(cls, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser(
            cls.name,
            help=(
                "Generate local MCP config and signing key, "
                "or print setup instructions."
            ),
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            default=False,
            help=(
                "Non-interactive: generate .env and a signing key without prompts. "
                "Existing keys are preserved (idempotent)."
            ),
        )
        parser.add_argument(
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
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help=(
                "Preview the change that --install would make, "
                "without writing any files."
            ),
        )
        parser.add_argument(
            "--env-file",
            metavar="PATH",
            default=None,
            help=(
                "Absolute path to the .env file to write (with --yes) or embed in "
                "the installed config (with --install). "
                "Defaults to '.env' in the current directory."
            ),
        )
        parser.add_argument(
            "--secrets-dir",
            metavar="DIR",
            default=None,
            help=(
                "Directory where the signing key is stored. "
                "Defaults to '.secrets' in the current directory."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help=(
                "Replace existing .env and signing key with fresh values. "
                "By default existing files are preserved (idempotent)."
            ),
        )

    @classmethod
    def run(cls, args: argparse.Namespace) -> int:
        yes: bool = getattr(args, "yes", False)
        install_target: str | None = getattr(args, "install", None)
        dry_run: bool = getattr(args, "dry_run", False)
        env_file_arg: str | None = getattr(args, "env_file", None)
        secrets_dir_arg: str | None = getattr(args, "secrets_dir", None)
        force: bool = getattr(args, "force", False)

        if install_target is not None:
            return cls._install(
                install_target,
                env_file_arg=env_file_arg,
                dry_run=dry_run,
                yes=yes,
            )

        if yes:
            return cls._generate_files(
                env_file_arg=env_file_arg,
                secrets_dir_arg=secrets_dir_arg,
                force=force,
            )

        cls._print_instructions()
        return 0

    @classmethod
    def _install(
        cls,
        target_name: str,
        *,
        env_file_arg: str | None,
        dry_run: bool,
        yes: bool,
    ) -> int:
        """Run the per-target install via the agent_targets registry."""
        from modal_mcp.agent_targets import get_target
        from modal_mcp.setup import DEFAULT_ENV_FILE

        if env_file_arg is not None:
            resolved_env = Path(env_file_arg).expanduser()
        else:
            resolved_env = Path(DEFAULT_ENV_FILE).expanduser().absolute()

        target = get_target(target_name)

        # Codex (stdio) embeds the env-file path verbatim and so requires
        # an absolute path; SSE targets (claude) read bind from the env file.
        if target_name == "codex":
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
                target.install(env_file=resolved_env, dry_run=dry_run, yes=yes)
            except (Exception, ValueError) as exc:
                # Re-raise non-install exceptions; install errors map to exit 1.
                if exc.__class__.__name__ not in {
                    "CodexInstallError",
                    "ValueError",
                }:
                    raise
                print(f"error: {exc}", file=sys.stderr)
                return 1
            return 0

        # SSE target (claude / claude_desktop)
        bind: str | None = None
        if resolved_env.exists():
            for line in resolved_env.read_text(encoding="utf-8").splitlines():
                if line.startswith("MODAL_MCP_HTTP_BIND="):
                    bind = line.split("=", 1)[1].strip()
                    break
        if bind is None:
            bind = os.environ.get("MODAL_MCP_HTTP_BIND")

        try:
            target.install(bind=bind, dry_run=dry_run, yes=yes)
        except (Exception, ValueError) as exc:
            if exc.__class__.__name__ not in {
                "ClaudeInstallError",
                "ValueError",
            }:
                raise
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    @classmethod
    def _generate_files(
        cls,
        *,
        env_file_arg: str | None,
        secrets_dir_arg: str | None,
        force: bool,
    ) -> int:
        from modal_mcp.domain.file_io import SetupFilesError
        from modal_mcp.setup import DEFAULT_ENV_FILE, DEFAULT_SECRETS_DIR, run_setup

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

    @staticmethod
    def _print_instructions() -> None:
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


__all__ = ["SetupCommand"]
