"""``modal-mcp stdio`` — start the MCP server over stdin/stdout."""

from __future__ import annotations

import argparse
from typing import ClassVar

from modal_mcp.cli._env import load_env_file


class StdioCommand:
    """Start the MCP server using stdio transport (for CLI clients)."""

    name: ClassVar[str] = "stdio"

    @classmethod
    def register(
        cls, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]
    ) -> None:
        parser = subparsers.add_parser(
            cls.name,
            help="Start the MCP server using stdio transport (for CLI clients).",
        )
        parser.add_argument(
            "--env-file",
            metavar="PATH",
            help="Path to a .env file to load before starting the server.",
        )

    @classmethod
    def run(cls, args: argparse.Namespace) -> int:
        load_env_file(args.env_file)

        from modal_mcp.server import run_stdio

        run_stdio()
        return 0


__all__ = ["StdioCommand"]
