"""Safe filesystem helpers for setup-generated secrets and configuration.

These helpers are used by the local-setup flow to write `.env` files,
signing keys, and other sensitive material without leaking secrets via
partial writes, world-readable permissions, or symlink attacks.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

# Private directory: owner rwx only
_PRIVATE_DIR_MODE = 0o700

# Secret file: owner rw only
_SECRET_FILE_MODE = 0o600


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

    if p.is_symlink():
        msg = f"path is a symlink and cannot be used as a private directory: {p}"
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
    if not overwrite and p.exists():
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


__all__ = [
    "SetupFilesError",
    "ensure_private_dir",
    "write_secret",
]
