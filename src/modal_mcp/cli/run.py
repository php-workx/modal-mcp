"""``modal-mcp run`` — start the MCP server (HTTP/SSE transport)."""

from __future__ import annotations

import argparse
from typing import ClassVar

from modal_mcp.cli._env import load_env_file


class RunCommand:
    """Start the MCP server, optionally loading an env file first."""

    name: ClassVar[str] = "run"

    @classmethod
    def register(
        cls, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]
    ) -> None:
        parser = subparsers.add_parser(cls.name, help="Start the MCP server.")
        parser.add_argument(
            "--env-file",
            metavar="PATH",
            help="Path to a .env file to load before starting the server.",
        )

    @classmethod
    def run(cls, args: argparse.Namespace) -> int:
        # ``getattr`` (not direct attribute access) is required here because
        # ``RunCommand.run`` is also the fallback handler for ``main([])`` (no
        # subcommand), which produces ``Namespace(subcommand=None)`` with no
        # ``env_file`` attribute set by argparse.
        load_env_file(getattr(args, "env_file", None))

        from modal_mcp.server import run

        run()
        return 0


__all__ = ["RunCommand"]
