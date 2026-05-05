"""Unit tests for modal_mcp.setup_files safe-write helpers."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from modal_mcp.setup_files import (
    SetupFilesError,
    ensure_gitignore_entries,
    ensure_private_dir,
    generate_signing_key,
    safe_write_text,
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


# ---------------------------------------------------------------------------
# safe_write_text
# ---------------------------------------------------------------------------


class TestSafeWriteText:
    def test_creates_file_with_content(self, tmp_path: Path) -> None:
        dest = tmp_path / "hello.txt"
        result = safe_write_text(dest, "hello world")
        assert result == dest
        assert dest.read_text() == "hello world"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        dest = tmp_path / "a" / "b" / "config.txt"
        safe_write_text(dest, "data")
        assert dest.exists()
        assert dest.read_text() == "data"

    def test_preserves_existing_by_default(self, tmp_path: Path) -> None:
        dest = tmp_path / "existing.txt"
        dest.write_text("original")
        safe_write_text(dest, "new_value")
        assert dest.read_text() == "original"

    def test_returns_path_for_preserved_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "keep.txt"
        dest.write_text("keep")
        result = safe_write_text(dest, "discard")
        assert result == dest

    def test_overwrite_replaces_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "replace.txt"
        dest.write_text("original")
        safe_write_text(dest, "replaced", overwrite=True)
        assert dest.read_text() == "replaced"

    def test_overwrite_false_on_new_file_writes_content(self, tmp_path: Path) -> None:
        dest = tmp_path / "new.txt"
        safe_write_text(dest, "fresh", overwrite=False)
        assert dest.read_text() == "fresh"

    def test_raises_on_symlink_target(self, tmp_path: Path) -> None:
        real_file = tmp_path / "real.txt"
        real_file.write_text("real")
        link = tmp_path / "link.txt"
        link.symlink_to(real_file)

        with pytest.raises(SetupFilesError, match="symlink"):
            safe_write_text(link, "injected")

        # The symlink target must be unchanged.
        assert real_file.read_text() == "real"

    def test_raises_on_dangling_symlink(self, tmp_path: Path) -> None:
        link = tmp_path / "dangling.txt"
        link.symlink_to(tmp_path / "nowhere.txt")

        with pytest.raises(SetupFilesError, match="symlink"):
            safe_write_text(link, "injected")

    def test_write_is_atomic_no_partial_content(self, tmp_path: Path) -> None:
        dest = tmp_path / "atomic.txt"
        safe_write_text(dest, "complete")
        assert dest.read_text() == "complete"
        leftovers = [
            f for f in tmp_path.iterdir() if f.name.startswith(".tmp_") and f != dest
        ]
        assert leftovers == [], f"leftover temp files: {leftovers}"

    def test_custom_encoding(self, tmp_path: Path) -> None:
        dest = tmp_path / "latin.txt"
        safe_write_text(dest, "café", encoding="utf-8")
        assert dest.read_text(encoding="utf-8") == "café"

    def test_does_not_restrict_file_permissions(self, tmp_path: Path) -> None:
        """safe_write_text does not impose 0o600; it is not a secrets helper."""
        dest = tmp_path / "public.txt"
        safe_write_text(dest, "public data")
        # The file should be readable (no PermissionError), whatever the umask.
        assert dest.read_text() == "public data"

    def test_expands_home_tilde(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        result = safe_write_text(Path("~/tilde_text.txt"), "val")
        assert result == tmp_path / "tilde_text.txt"
        assert result.read_text() == "val"


# ---------------------------------------------------------------------------
# ensure_gitignore_entries
# ---------------------------------------------------------------------------


class TestEnsureGitignoreEntries:
    def test_adds_default_entries_to_new_file(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        changed = ensure_gitignore_entries(gitignore)

        assert changed is True
        lines = gitignore.read_text().splitlines()
        assert ".env" in lines
        assert ".env.*" in lines
        assert ".secrets/" in lines

    def test_entries_appear_exactly_once(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        ensure_gitignore_entries(gitignore)
        content = gitignore.read_text()
        assert content.count(".env\n") == 1
        assert content.count(".env.*\n") == 1
        assert content.count(".secrets/\n") == 1

    def test_second_call_returns_false(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        ensure_gitignore_entries(gitignore)
        changed = ensure_gitignore_entries(gitignore)
        assert changed is False

    def test_second_call_does_not_duplicate_lines(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        ensure_gitignore_entries(gitignore)
        ensure_gitignore_entries(gitignore)
        content = gitignore.read_text()
        assert content.count(".env\n") == 1
        assert content.count(".env.*\n") == 1
        assert content.count(".secrets/\n") == 1

    def test_appends_to_existing_gitignore(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n__pycache__/\n")
        changed = ensure_gitignore_entries(gitignore)

        assert changed is True
        content = gitignore.read_text()
        assert "*.pyc" in content
        assert "__pycache__/" in content
        assert ".env" in content

    def test_skips_already_present_entries(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".env\n.env.*\n.secrets/\n")
        changed = ensure_gitignore_entries(gitignore)
        assert changed is False

    def test_only_adds_missing_entries(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".env\n")
        changed = ensure_gitignore_entries(gitignore)

        assert changed is True
        lines = gitignore.read_text().splitlines()
        assert lines.count(".env") == 1  # not duplicated
        assert ".env.*" in lines
        assert ".secrets/" in lines

    def test_custom_entries(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        changed = ensure_gitignore_entries(gitignore, entries=["dist/", "build/"])

        assert changed is True
        lines = gitignore.read_text().splitlines()
        assert "dist/" in lines
        assert "build/" in lines

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        gitignore = tmp_path / "subdir" / ".gitignore"
        ensure_gitignore_entries(gitignore)
        assert gitignore.exists()

    def test_raises_on_symlink_target(self, tmp_path: Path) -> None:
        real_file = tmp_path / "real.gitignore"
        real_file.write_text("*.pyc\n", encoding="utf-8")
        link = tmp_path / ".gitignore"
        link.symlink_to(real_file)

        with pytest.raises(SetupFilesError, match="symlink"):
            ensure_gitignore_entries(link)

        assert real_file.read_text(encoding="utf-8") == "*.pyc\n"

    def test_file_ends_with_newline(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        ensure_gitignore_entries(gitignore)
        assert gitignore.read_text().endswith("\n")

    def test_preserves_file_without_trailing_newline(self, tmp_path: Path) -> None:
        """File lacking trailing newline gets one inserted before new entries."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.log")  # no trailing newline
        ensure_gitignore_entries(gitignore, entries=["dist/"])
        content = gitignore.read_text()
        assert "*.log\n" in content
        assert "dist/" in content


# ---------------------------------------------------------------------------
# generate_signing_key
# ---------------------------------------------------------------------------

_KID_HEX_RE = re.compile(r"^[^:]+:[0-9a-f]{64}$")


class TestGenerateSigningKey:
    def test_format_matches_kid_hex(self) -> None:
        result = generate_signing_key("kid1")
        assert _KID_HEX_RE.match(result), f"unexpected format: {result!r}"

    def test_starts_with_given_kid(self) -> None:
        result = generate_signing_key("kid1")
        assert result.startswith("kid1:")

    def test_hex_part_is_64_chars(self) -> None:
        result = generate_signing_key("mykey")
        _, hex_part = result.split(":", 1)
        assert len(hex_part) == 64

    def test_hex_part_parses_as_32_bytes(self) -> None:
        result = generate_signing_key("kid1")
        _, hex_part = result.split(":", 1)
        raw = bytes.fromhex(hex_part)
        assert len(raw) == 32

    def test_hex_part_is_lowercase(self) -> None:
        result = generate_signing_key("k1")
        _, hex_part = result.split(":", 1)
        assert hex_part == hex_part.lower()

    def test_different_kids_embed_correctly(self) -> None:
        for kid in ("k1", "mykey", "signing-key-2025"):
            result = generate_signing_key(kid)
            assert result.startswith(f"{kid}:")

    def test_successive_calls_produce_different_keys(self) -> None:
        """Keys must be cryptographically random; two calls should not collide."""
        r1 = generate_signing_key("k")
        r2 = generate_signing_key("k")
        assert r1 != r2, "two successive calls returned identical key material"
