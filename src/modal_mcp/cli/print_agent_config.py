"""``modal-mcp print-agent-config`` — print the agent client configuration block."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import ClassVar


class PrintAgentConfigCommand:
    """Print the agent client configuration block."""

    name: ClassVar[str] = "print-agent-config"

    @classmethod
    def register(
        cls, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]
    ) -> None:
        parser = subparsers.add_parser(
            cls.name,
            help="Print the agent client configuration block.",
        )
        parser.add_argument(
            "--target",
            choices=["claude", "claude_desktop", "codex"],
            default="claude",
            help="Agent target to print config for (default: claude).",
        )
        parser.add_argument(
            "--env-file",
            metavar="PATH",
            default=None,
            help=(
                "Absolute path to the .env file to embed in the config snippet. "
                "Required for --target codex."
            ),
        )

    @classmethod
    def run(cls, args: argparse.Namespace) -> int:
        from modal_mcp.agent_targets import get_target

        target_name: str = getattr(args, "target", "claude") or "claude"
        env_file_arg: str | None = getattr(args, "env_file", None)
        env_file: Path | None = (
            Path(env_file_arg).expanduser().absolute()
            if env_file_arg is not None
            else None
        )
        target = get_target(target_name)
        target.render(env_file=env_file, file=sys.stdout)
        return 0


__all__ = ["PrintAgentConfigCommand"]
