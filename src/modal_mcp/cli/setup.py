"""``modal-mcp setup`` — generate config + signing key, or install a target."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import ClassVar


class SetupCommand:
    """Generate local MCP config and signing key, or print setup instructions."""

    name: ClassVar[str] = "setup"

    @classmethod
    def register(
        cls, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]
    ) -> None:
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
        env_file_arg: str | None = getattr(args, "env_file", None)
        secrets_dir_arg: str | None = getattr(args, "secrets_dir", None)
        force: bool = getattr(args, "force", False)

        if install_target is not None:
            return cls._install(install_target, args)

        if yes:
            return cls._generate_files(
                env_file_arg=env_file_arg,
                secrets_dir_arg=secrets_dir_arg,
                force=force,
            )

        cls._print_instructions()
        return 0

    @classmethod
    def _install(cls, target_name: str, args: argparse.Namespace) -> int:
        """Dispatch the install to the named agent target.

        Each target module owns its own argument parsing and exception
        handling via :func:`install_from_cli`; this method is a uniform
        dispatch with no per-target branching.
        """
        from modal_mcp.agent_targets import get_target

        target = get_target(target_name)
        exit_code: int = target.install_from_cli(args)
        return exit_code

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
