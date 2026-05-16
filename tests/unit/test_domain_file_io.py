"""Tests pinning the new domain.file_io module location."""

from __future__ import annotations


def test_domain_file_io_exposes_all_helpers() -> None:
    """The new home for file-I/O primitives is :mod:`modal_mcp.domain.file_io`."""
    from modal_mcp.domain.file_io import (
        SetupFilesError,
        ensure_gitignore_entries,
        ensure_private_dir,
        generate_signing_key,
        safe_write_text,
        write_secret,
    )

    assert SetupFilesError is not None
    assert callable(ensure_gitignore_entries)
    assert callable(ensure_private_dir)
    assert callable(generate_signing_key)
    assert callable(safe_write_text)
    assert callable(write_secret)
