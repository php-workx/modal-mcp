"""Smoke tests for the package scaffold."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import modal_mcp


def test_package_import_smoke() -> None:
    """The package imports cleanly."""
    assert modal_mcp.__name__ == "modal_mcp"
