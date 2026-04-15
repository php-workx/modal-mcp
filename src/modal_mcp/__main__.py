"""CLI entrypoint for modal_mcp."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="modal-mcp",
        description="Modal MCP server shell.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI shell."""
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
