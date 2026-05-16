"""Tests for the CliCommand registry and dispatch."""

from __future__ import annotations

import importlib

import pytest


def test_cli_command_protocol_exists() -> None:
    from modal_mcp.cli import CliCommand

    assert hasattr(CliCommand, "register")
    assert hasattr(CliCommand, "run")


def test_registry_lists_all_five_commands() -> None:
    from modal_mcp.cli import COMMANDS

    names = {c.name for c in COMMANDS}
    assert names == {"run", "stdio", "setup", "doctor", "print-agent-config"}


@pytest.mark.parametrize(
    ("module_path", "class_name", "expected_name"),
    [
        ("modal_mcp.cli.run", "RunCommand", "run"),
        ("modal_mcp.cli.stdio", "StdioCommand", "stdio"),
        ("modal_mcp.cli.setup", "SetupCommand", "setup"),
        ("modal_mcp.cli.doctor", "DoctorCommand", "doctor"),
        (
            "modal_mcp.cli.print_agent_config",
            "PrintAgentConfigCommand",
            "print-agent-config",
        ),
    ],
)
def test_command_class_present(
    module_path: str, class_name: str, expected_name: str
) -> None:
    # Safe: module_path values are static parametrize literals above, never user input.
    # fmt: off
    module = importlib.import_module(module_path)  # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import  # noqa: E501
    # fmt: on
    command = getattr(module, class_name)
    assert command.name == expected_name


def test_main_dispatches_to_run_with_no_subcommand(monkeypatch) -> None:
    """No subcommand defaults to RunCommand (backward-compat)."""
    from modal_mcp import __main__ as main_mod

    called: list[bool] = []
    monkeypatch.setattr("modal_mcp.server.run", lambda: called.append(True))
    assert main_mod.main([]) == 0
    assert called == [True]


def test_get_target_returns_codex_module() -> None:
    from modal_mcp.agent_targets import codex, get_target

    assert get_target("codex") is codex
    assert get_target("CODEX") is codex


def test_get_target_returns_claude_module_for_aliases() -> None:
    from modal_mcp.agent_targets import claude, get_target

    assert get_target("claude") is claude
    assert get_target("claude_desktop") is claude


def test_get_target_raises_on_unknown_name() -> None:
    from modal_mcp.agent_targets import get_target

    with pytest.raises(ValueError, match="Unknown agent target"):
        get_target("nope")
