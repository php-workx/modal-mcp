"""DEPRECATED — re-exports from :mod:`modal_mcp.domain.file_io`.

This module is preserved as an import shim during the
``epo-collapse-cli-plumbing-into-agent-g76h`` migration so that external
imports (e.g. ``from modal_mcp.setup_files import write_secret``) keep
working for one release.  Will be deleted in a later step of the
collapse-cli-plumbing plan.
"""

from __future__ import annotations

from modal_mcp.domain.file_io import (
    SetupFilesError,
    ensure_gitignore_entries,
    ensure_private_dir,
    generate_signing_key,
    safe_write_text,
    write_secret,
)

__all__ = [
    "SetupFilesError",
    "ensure_gitignore_entries",
    "ensure_private_dir",
    "generate_signing_key",
    "safe_write_text",
    "write_secret",
]
