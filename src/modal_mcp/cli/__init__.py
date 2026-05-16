"""CLI subcommand registry for modal-mcp.

Each subcommand is a class implementing the :class:`CliCommand` protocol:

* ``name`` (ClassVar str) — the argparse subcommand name.
* ``register(subparsers) -> None`` — add a subparser and arguments.
* ``run(args) -> int`` — execute the command and return an exit code.

:data:`COMMANDS` lists every command in registration order.  ``__main__.py``
iterates this list to populate the parser and to look up handlers.
"""

from __future__ import annotations

import argparse
from typing import ClassVar, Protocol, runtime_checkable

from modal_mcp.cli.doctor import DoctorCommand
from modal_mcp.cli.print_agent_config import PrintAgentConfigCommand
from modal_mcp.cli.run import RunCommand
from modal_mcp.cli.setup import SetupCommand
from modal_mcp.cli.stdio import StdioCommand


@runtime_checkable
class CliCommand(Protocol):
    """Protocol implemented by every CLI subcommand."""

    name: ClassVar[str]

    @classmethod
    def register(cls, subparsers: argparse._SubParsersAction) -> None: ...

    @classmethod
    def run(cls, args: argparse.Namespace) -> int: ...


COMMANDS: tuple[type[CliCommand], ...] = (
    RunCommand,
    StdioCommand,
    SetupCommand,
    DoctorCommand,
    PrintAgentConfigCommand,
)


__all__ = ["COMMANDS", "CliCommand"]
