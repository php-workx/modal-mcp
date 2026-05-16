"""Cross-module invariant: Codex args[0] must be a registered CLI subcommand.

This test is the regression catcher for the May 2026 stdio-drop incident
where ``CODEX_SERVER_ARGS_TEMPLATE`` said ``("run", "--env-file", ...)`` but
``modal-mcp run`` started uvicorn HTTP instead of MCP stdio.  Codex spawned
the binary, expected a JSON-RPC handshake on stdout, and timed out.

The invariant: whatever subcommand the Codex install writes into
``~/.codex/config.toml`` MUST exist in ``modal-mcp``'s argparse parser AND
MUST be a transport suitable for stdio subprocess launch.  This test fails
loudly if anyone changes one side of that contract without the other.
"""

from __future__ import annotations

import argparse

from modal_mcp.__main__ import build_parser
from modal_mcp.agent_targets.codex import CODEX_SERVER_ARGS_TEMPLATE


def _registered_subcommand_names() -> set[str]:
    """Return the set of subcommand names registered on the modal-mcp parser."""
    parser = build_parser()
    names: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    return names


def test_codex_args_first_token_is_a_registered_subcommand() -> None:
    """The first token of CODEX_SERVER_ARGS_TEMPLATE must resolve to a real subcommand.

    If this fails, someone updated the argparse parser without updating
    CODEX_SERVER_ARGS_TEMPLATE (or vice versa). Codex installs will time out
    because the spawned process either fails to parse args or starts the
    wrong transport.
    """
    registered = _registered_subcommand_names()
    first_token = CODEX_SERVER_ARGS_TEMPLATE[0]
    assert first_token in registered, (
        f"CODEX_SERVER_ARGS_TEMPLATE[0]={first_token!r} is not a registered "
        f"modal-mcp subcommand. Registered: {sorted(registered)!r}. "
        "Codex installs will time out on the MCP initialize handshake. "
        "Either restore the missing subcommand or update the args template."
    )


def test_codex_args_first_token_is_a_stdio_transport_subcommand() -> None:
    """The first token must be 'stdio', not 'run'.

    'run' starts uvicorn HTTP. Codex needs MCP stdio transport. Any other
    value is a bug. Pinning the literal here is intentional: it forces the
    author of any future transport-renaming PR to confront the Codex install
    contract head-on.
    """
    assert CODEX_SERVER_ARGS_TEMPLATE[0] == "stdio", (
        f"CODEX_SERVER_ARGS_TEMPLATE[0]={CODEX_SERVER_ARGS_TEMPLATE[0]!r}; "
        "expected 'stdio'. Codex launches modal-mcp as a stdio subprocess, "
        "not as an HTTP server."
    )
