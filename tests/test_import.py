"""Smoke tests for the package scaffold."""

from __future__ import annotations

import modal_mcp


def test_package_import_smoke() -> None:
    """The package imports cleanly."""
    assert modal_mcp.__name__ == "modal_mcp"
