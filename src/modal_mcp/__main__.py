"""CLI entrypoint for modal_mcp.

Builds the argparse parser by asking every :class:`~modal_mcp.cli.CliCommand`
in :data:`~modal_mcp.cli.COMMANDS` to register itself, then dispatches the
chosen subcommand back to that command's ``run`` classmethod.

When no subcommand is given (``modal-mcp`` with no args), the dispatcher
falls back to :class:`~modal_mcp.cli.run.RunCommand` for backward compatibility.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from modal_mcp.cli import COMMANDS, CliCommand

# When no subcommand is given on the CLI, fall back to ``run`` so that existing
# users running bare ``modal-mcp`` continue to get a server.
_DEFAULT_SUBCOMMAND = "run"

_BY_NAME: dict[str, type[CliCommand]] = {c.name: c for c in COMMANDS}


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser by letting each command register its subparser."""
    parser = argparse.ArgumentParser(
        prog="modal-mcp",
        description="Modal MCP server shell.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")
    for command in COMMANDS:
        command.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI shell."""
    args = build_parser().parse_args(argv)
    subcommand = args.subcommand or _DEFAULT_SUBCOMMAND
    command = _BY_NAME[subcommand]
    return command.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
