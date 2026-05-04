"""Safe filesystem helpers for setup-generated secrets and configuration.

These helpers are used by the local-setup flow to write `.env` files,
signing keys, and other sensitive material without leaking secrets via
partial writes, world-readable permissions, or symlink attacks.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import tempfile
from collections.abc import Sequence
from pathlib import Path

# Private directory: owner rwx only
_PRIVATE_DIR_MODE = 0o700

# Secret file: owner rw only
_SECRET_FILE_MODE = 0o600

#: HMAC signing key length in bytes (32 bytes → 64 hex chars for HMAC-SHA256).
_SIGNING_KEY_BYTES: int = 32

#: Default entries added by :func:`ensure_gitignore_entries`.
_DEFAULT_GITIGNORE_ENTRIES: tuple[str, ...] = (".env", ".env.*", ".secrets/")


class SetupFilesError(OSError):
    """Raised when a safe-write invariant is violated (e.g. symlink target)."""


def ensure_private_dir(path: Path | str) -> Path:
    """Create *path* as a private directory (mode 0o700) and return it.

    Rules:
    - If *path* is a symlink, raises :class:`SetupFilesError` immediately.
    - Parent directories are created as needed (``parents=True``).
    - If the directory already exists its mode is tightened to 0o700 on POSIX.
    - On non-POSIX systems the directory is created but no mode is enforced.

    Returns the :class:`~pathlib.Path` of the created/existing directory.
    """
    p = Path(path).expanduser()

    for ancestor in [p, *p.parents]:
        if ancestor.exists() and ancestor.is_symlink():
            msg = f"path contains a symlinked ancestor: {ancestor}"
            raise SetupFilesError(msg)

    p.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)

    if os.name == "posix":
        # Explicit chmod so that a restrictive umask cannot widen the mode later,
        # and so that we tighten permissions on pre-existing directories.
        p.chmod(_PRIVATE_DIR_MODE)

    return p


def write_secret(
    path: Path | str,
    content: str | bytes,
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically write secret *content* to *path* with mode 0o600.

    Guarantees:
    - **Symlink refusal**: raises :class:`SetupFilesError` if *path* (or a
      racing rename) resolves to a symlink; never follows a symlink to overwrite
      an unintended destination.
    - **Atomic write**: content is written to a sibling temp file which is then
      renamed into place, so readers never observe a partial file.
    - **Private parent**: the parent directory is created via
      :func:`ensure_private_dir` (mode 0o700) when it does not exist.
    - **Preserve by default**: when *overwrite* is ``False`` (the default) and
      *path* already exists the function returns immediately without touching
      the file — existing signing keys are left intact.

    Parameters
    ----------
    path:
        Destination path for the secret file.
    content:
        The secret material to write (str is UTF-8 encoded).
    overwrite:
        When ``True`` an existing file is replaced atomically.  Defaults to
        ``False`` so that pre-existing keys are preserved.

    Returns
    -------
    Path
        The resolved path that was written (or preserved).

    Raises
    ------
    SetupFilesError
        If *path* is or becomes a symlink, or if the parent dir is a symlink.
    """
    p = Path(path).expanduser()

    # Reject symlink targets up-front.
    if p.is_symlink():
        msg = f"refusing to write secret: target is a symlink: {p}"
        raise SetupFilesError(msg)

    # Preserve existing file unless the caller explicitly requests overwrite.
    if not overwrite and p.is_file():
        return p

    # Ensure a private parent directory exists.
    ensure_private_dir(p.parent)

    encoded: bytes = content.encode() if isinstance(content, str) else content

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp_")
        tmp_path = Path(tmp_str)
        try:
            if os.name == "posix":
                # Restrict permissions *before* writing so the data is never
                # visible to other users even momentarily.
                os.chmod(fd, _SECRET_FILE_MODE)
            os.write(fd, encoded)
        finally:
            os.close(fd)

        # Race-condition guard: re-check that the destination has not become a
        # symlink between our initial check and the rename.
        if p.is_symlink():
            msg = f"refusing to write secret: target became a symlink: {p}"
            raise SetupFilesError(msg)

        # Atomic replacement.
        tmp_path.replace(p)
        tmp_path = None  # rename succeeded; suppress cleanup
    finally:
        # Clean up the temp file if something went wrong before the rename.
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()

    # Tighten mode on the destination in case rename widened it (e.g. on
    # non-atomic FAT-style filesystems or after a cross-device move).
    if os.name == "posix":
        p.chmod(_SECRET_FILE_MODE)

    return p


def safe_write_text(
    path: Path | str,
    content: str,
    *,
    overwrite: bool = False,
    encoding: str = "utf-8",
) -> Path:
    """Atomically write *content* to *path* as a plain text file.

    Unlike :func:`write_secret` this helper does **not** enforce private
    directory or file permissions; it is suitable for non-secret files such as
    ``.gitignore`` or configuration files placed in a shared project root.

    Guarantees:
    - **Symlink refusal**: raises :class:`SetupFilesError` if *path* is a
      symlink (checked before and after the write to guard against races).
    - **Atomic write**: content is written to a sibling temp file which is then
      renamed into place; readers never observe a partial file.
    - **Preserve by default**: when *overwrite* is ``False`` (the default) and
      *path* already exists the function returns immediately without touching
      the file.

    Parameters
    ----------
    path:
        Destination path for the file.
    content:
        Text content to write.
    overwrite:
        When ``True`` an existing file is replaced atomically.  Defaults to
        ``False`` so that pre-existing files are preserved.
    encoding:
        Text encoding used to convert *content* to bytes.  Defaults to
        ``utf-8``.

    Returns
    -------
    Path
        The resolved path that was written (or preserved).

    Raises
    ------
    SetupFilesError
        If *path* is or becomes a symlink.
    """
    p = Path(path).expanduser()

    if p.is_symlink():
        msg = f"refusing to write: target is a symlink: {p}"
        raise SetupFilesError(msg)

    if not overwrite and p.exists():
        return p

    p.parent.mkdir(parents=True, exist_ok=True)

    encoded = content.encode(encoding)
    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp_")
        tmp_path = Path(tmp_str)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)

        # Race-condition guard: re-check that the destination has not become a
        # symlink between our initial check and the rename.
        if p.is_symlink():
            msg = f"refusing to write: target became a symlink: {p}"
            raise SetupFilesError(msg)

        tmp_path.replace(p)
        tmp_path = None  # rename succeeded; suppress cleanup
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()

    return p


def ensure_gitignore_entries(
    path: Path | str,
    entries: Sequence[str] = _DEFAULT_GITIGNORE_ENTRIES,
) -> bool:
    """Add *entries* to a gitignore file at *path* idempotently.

    Entries already present in the file are skipped.  The file is created if it
    does not exist.  Missing entries are appended at the end, each on its own
    line.

    The default *entries* cover the three standard setup artefacts:

    - ``.env``
    - ``.env.*``
    - ``.secrets/``

    Parameters
    ----------
    path:
        Path to the ``.gitignore`` file to update (or create).
    entries:
        Lines to ensure are present.  Defaults to :data:`_DEFAULT_GITIGNORE_ENTRIES`.

    Returns
    -------
    bool
        ``True`` when at least one entry was added and the file was modified;
        ``False`` when all entries were already present (file unchanged).
    """
    p = Path(path).expanduser()
    if p.is_symlink():
        msg = f"refusing to update gitignore: target is a symlink: {p}"
        raise SetupFilesError(msg)

    existing_text = p.read_text(encoding="utf-8") if p.exists() else ""
    existing_lines = existing_text.splitlines()
    # Strip whitespace for comparison to handle trailing spaces and CRLF line endings.
    existing_stripped = {line.strip() for line in existing_lines}

    to_add = [e for e in entries if e not in existing_stripped]

    if not to_add:
        return False

    # Append missing entries, ensuring the file ends with a newline before them.
    new_content = existing_text
    if new_content and not new_content.endswith("\n"):
        new_content += "\n"
    new_content += "\n".join(to_add) + "\n"

    safe_write_text(p, new_content, overwrite=True)

    return True


def generate_signing_key(kid: str) -> str:
    """Return a fresh HMAC-SHA256 signing key in ``kid:hex`` format.

    The hex portion is 64 lowercase hexadecimal characters derived from 32
    cryptographically random bytes, giving a 256-bit key suitable for
    HMAC-SHA256.

    Parameters
    ----------
    kid:
        Key ID to embed in the returned string.

    Returns
    -------
    str
        A string of the form ``<kid>:[0-9a-f]{64}``.
    """
    key_bytes = secrets.token_bytes(_SIGNING_KEY_BYTES)
    return f"{kid}:{key_bytes.hex()}"


__all__ = [
    "SetupFilesError",
    "ensure_gitignore_entries",
    "ensure_private_dir",
    "generate_signing_key",
    "safe_write_text",
    "write_secret",
]
