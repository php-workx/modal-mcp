"""Unit tests for the modal-mcp CLI entrypoint."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

from modal_mcp.__main__ import main


def test_main_delegates_to_server_run() -> None:
    """main([]) must invoke modal_mcp.server.run() exactly once."""
    with patch("modal_mcp.server.run") as mock_run:
        result = main([])
    mock_run.assert_called_once_with()
    assert result == 0


def test_pyproject_console_script_points_to_cli_main() -> None:
    """pyproject.toml console script must remain modal_mcp.__main__:main."""
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    scripts = data["project"]["scripts"]
    assert scripts.get("modal-mcp") == "modal_mcp.__main__:main"
