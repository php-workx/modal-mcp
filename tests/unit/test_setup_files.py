"""Unit tests for modal_mcp.setup_files safe-write helpers."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from modal_mcp.setup_files import (
    SetupFilesError,
    ensure_private_dir,
    write_secret,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mode(path: Path) -> int:
    """Return the low 9 permission bits of *path*."""
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# ensure_private_dir
# ---------------------------------------------------------------------------


class TestEnsurePrivateDir:
    def test_creates_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "secrets"
        result = ensure_private_dir(target)
        assert target.is_dir()
        assert result == target

    def test_creates_nested_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c"
        ensure_private_dir(target)
        assert target.is_dir()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_directory_mode_is_private(self, tmp_path: Path) -> None:
        target = tmp_path / "priv"
        ensure_private_dir(target)
        assert _mode(target) == 0o700

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_tightens_existing_directory_permissions(self, tmp_path: Path) -> None:
        target = tmp_path / "priv"
        target.mkdir(mode=0o755)
        assert _mode(target) == 0o755

        ensure_private_dir(target)
        assert _mode(target) == 0o700

    def test_idempotent_on_existing_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "priv"
        ensure_private_dir(target)
        # Should not raise on second call.
        result = ensure_private_dir(target)
        assert result == target

    def test_raises_on_symlink(self, tmp_path: Path) -> None:
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real_dir)

        with pytest.raises(SetupFilesError, match="symlink"):
            ensure_private_dir(link)

    def test_expands_home_tilde(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        target = ensure_private_dir(Path("~/test_priv_dir"))
        assert target == tmp_path / "test_priv_dir"
        assert target.is_dir()


# ---------------------------------------------------------------------------
# write_secret
# ---------------------------------------------------------------------------


class TestWriteSecret:
    def test_creates_file_with_content(self, tmp_path: Path) -> None:
        dest = tmp_path / "token.txt"
        result = write_secret(dest, "s3cr3t")
        assert result == dest
        assert dest.read_text() == "s3cr3t"

    def test_accepts_bytes_content(self, tmp_path: Path) -> None:
        dest = tmp_path / "key.bin"
        write_secret(dest, b"\x00\xff\xab")
        assert dest.read_bytes() == b"\x00\xff\xab"

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_file_mode_is_secret(self, tmp_path: Path) -> None:
        dest = tmp_path / "secret.txt"
        write_secret(dest, "hello")
        assert _mode(dest) == 0o600

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_parent_dir_is_private(self, tmp_path: Path) -> None:
        dest = tmp_path / "subdir" / "secret.txt"
        write_secret(dest, "hello")
        assert _mode(dest.parent) == 0o700

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        dest = tmp_path / "a" / "b" / "secret.txt"
        write_secret(dest, "data")
        assert dest.exists()

    def test_preserves_existing_by_default(self, tmp_path: Path) -> None:
        dest = tmp_path / "key.pem"
        dest.write_text("original")
        write_secret(dest, "new_value")
        # overwrite=False → original content preserved
        assert dest.read_text() == "original"

    def test_overwrite_replaces_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "key.pem"
        dest.write_text("original")
        write_secret(dest, "replaced", overwrite=True)
        assert dest.read_text() == "replaced"

    def test_raises_on_symlink_target(self, tmp_path: Path) -> None:
        real_file = tmp_path / "real.txt"
        real_file.write_text("real")
        link = tmp_path / "link.txt"
        link.symlink_to(real_file)

        with pytest.raises(SetupFilesError, match="symlink"):
            write_secret(link, "injected")
        # Original file must be untouched.
        assert real_file.read_text() == "real"

    def test_raises_on_symlink_to_nonexistent(self, tmp_path: Path) -> None:
        link = tmp_path / "dangling.txt"
        link.symlink_to(tmp_path / "nowhere.txt")

        with pytest.raises(SetupFilesError, match="symlink"):
            write_secret(link, "injected")

    def test_write_is_atomic_no_partial_content(self, tmp_path: Path) -> None:
        """A temp file is used; no partial file should appear at destination."""
        dest = tmp_path / "atomic.txt"
        write_secret(dest, "complete")
        # If we got here without error the rename succeeded → content is full.
        assert dest.read_text() == "complete"
        # No leftover temp files in the directory.
        leftovers = [
            f for f in tmp_path.iterdir() if f.name.startswith(".tmp_") and f != dest
        ]
        assert leftovers == [], f"leftover temp files: {leftovers}"

    def test_expands_home_tilde(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        result = write_secret(Path("~/tilde_secret.txt"), "val")
        assert result == tmp_path / "tilde_secret.txt"
        assert result.read_text() == "val"

    def test_returns_path_for_preserved_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "existing.txt"
        dest.write_text("keep")
        result = write_secret(dest, "discard")
        assert result == dest

    def test_overwrite_false_on_new_file_writes_content(self, tmp_path: Path) -> None:
        dest = tmp_path / "new.txt"
        write_secret(dest, "fresh", overwrite=False)
        assert dest.read_text() == "fresh"
