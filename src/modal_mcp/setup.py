"""Non-interactive setup generation and credential-safety helpers for modal-mcp.

This module implements ``modal-mcp setup --yes``, which generates:

- A private HMAC signing key in ``<secrets_dir>/<signing_key_name>``
- A local ``.env`` file that references the key and supplies minimal
  server settings

It also provides interactive-setup guards:

- :func:`detect_modal_toml` — detect existing personal Modal credentials.
- :func:`warn_if_modal_toml_present` — emit a warning when ``~/.modal.toml``
  is found, so operators know they are risking credential reuse.
- :func:`require_tty_for_credential_choice` — refuse credential-choice
  operations in non-TTY environments where interactive confirmation is
  impossible.
- :func:`write_service_token_files` — store service-user token material as
  file-backed secrets (never in env vars or ``.env`` files).

Rules
-----
- No Modal credential material is ever written to a ``.env`` file
  (``MODAL_TOKEN_ID``, ``MODAL_TOKEN_SECRET``, ``MODAL_TOKEN_ID_FILE``,
  ``MODAL_TOKEN_SECRET_FILE``).
- ``MODAL_ENVIRONMENT`` is never written, so no default environment is
  silently pinned.
- Existing signing keys are preserved by default (idempotent).
- Symlink targets are refused for both the key file and the env file.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from modal_mcp.setup_files import (
    SetupFilesError,
    ensure_gitignore_entries,
    write_secret,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: HMAC key length in bytes (32 bytes → 256-bit key for HMAC-SHA256).
_SIGNING_KEY_BYTES: int = 32

#: Default key ID embedded in generated signing key entries.
_DEFAULT_KID: str = "k1"

#: Default allowed origin (matches the default ``MODAL_MCP_HTTP_BIND``).
_DEFAULT_ALLOWED_ORIGIN: str = "http://127.0.0.1:8765"

#: Default .env filename (resolved relative to CWD at call time).
DEFAULT_ENV_FILE: Path = Path(".env")

#: Default secrets directory (resolved relative to CWD at call time).
DEFAULT_SECRETS_DIR: Path = Path(".secrets")

#: Default signing key filename inside the secrets directory.
DEFAULT_SIGNING_KEY_NAME: str = "signing-key.txt"

#: Env-var names that must never appear in a setup-generated .env file.
#: Writing them would silently adopt Modal credentials or pin an environment.
FORBIDDEN_SETUP_ENV_KEYS: frozenset[str] = frozenset(
    {
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "MODAL_TOKEN_ID_FILE",
        "MODAL_TOKEN_SECRET_FILE",
        "MODAL_ENVIRONMENT",
    }
)

#: Warning emitted when ``~/.modal.toml`` is detected during interactive setup.
#: Existing Modal credentials in that file are typically personal/admin tokens;
#: reusing them for the MCP server may grant overly broad permissions.
MODAL_TOML_CREDENTIAL_WARNING: str = (
    "WARNING: Existing Modal credentials found in ~/.modal.toml.\n"
    "These credentials may belong to a personal editor or admin account.\n"
    "Using them for the MCP server could grant overly broad permissions.\n"
    "Consider creating a dedicated service-user token and storing it as\n"
    "file-backed secrets so credentials are isolated to this server."
)

#: Default filename for a file-backed Modal token ID secret.
DEFAULT_TOKEN_ID_NAME: str = "modal-token-id.txt"

#: Default filename for a file-backed Modal token secret.
DEFAULT_TOKEN_SECRET_NAME: str = "modal-token-secret.txt"


# ---------------------------------------------------------------------------
# Credential-safety exceptions
# ---------------------------------------------------------------------------


class CredentialChoiceError(ValueError):
    """Raised when a credential choice cannot be made safely.

    This is used in non-TTY environments where interactive confirmation is
    impossible and no explicit safe override has been provided.
    """


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SetupResult:
    """Outcome of a :func:`run_setup` invocation."""

    #: Resolved absolute path to the ``.env`` file.
    env_file: Path
    #: Resolved absolute path to the signing key file.
    signing_key_file: Path
    #: ``True`` when the signing key was freshly generated; ``False`` when a
    #: pre-existing key was preserved.
    signing_key_created: bool
    #: ``True`` when the ``.env`` file was freshly written; ``False`` when a
    #: pre-existing file was preserved.
    env_created: bool
    #: ``True`` when ``.gitignore`` was modified to add setup artifact entries;
    #: ``False`` when all entries were already present or no entries were added.
    gitignore_updated: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _generate_signing_key_material(kid: str = _DEFAULT_KID) -> str:
    """Return a fresh ``kid:hex`` HMAC-SHA256 signing key string."""
    key_bytes = secrets.token_bytes(_SIGNING_KEY_BYTES)
    return f"{kid}:{key_bytes.hex()}"


def _has_env_key(content: str, key: str) -> bool:
    """Check if *key* is already assigned in dotenv *content*."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith(f"{key}="):
            return True
    return False


def _build_env_content(signing_key_file: Path) -> str:
    """Return the content for a freshly generated ``.env`` file.

    The file only writes keys required for the server to start (signing key
    reference and allowed origins).  All Modal credential variables and
    ``MODAL_ENVIRONMENT`` are intentionally omitted.
    """
    lines = (
        "# modal-mcp local configuration",
        "# Generated by 'modal-mcp setup --yes'.",
        "#",
        "# DO NOT add raw MODAL_TOKEN_ID, MODAL_TOKEN_SECRET,",
        "# or MODAL_ENVIRONMENT here.",
        "# Prefer MODAL_TOKEN_ID_FILE and MODAL_TOKEN_SECRET_FILE",
        "# after creating a dedicated token.",
        "#",
        f"MODAL_MCP_SIGNING_KEY_FILE={signing_key_file}",
        f"MODAL_MCP_ALLOWED_ORIGINS={_DEFAULT_ALLOWED_ORIGIN}",
        "",
    )
    return "\n".join(lines)


def _write_env_idempotent(path: Path, content: str, *, overwrite: bool = False) -> bool:
    """Atomically write a dotenv file.

    Unlike :func:`~modal_mcp.setup_files.write_secret`, this helper does
    **not** tighten parent-directory permissions, making it suitable for files
    placed in a shared project root.

    The parent directory is created (``parents=True``) when it does not exist,
    but its mode is left unchanged.

    Parameters
    ----------
    path:
        Destination path for the ``.env`` file.
    content:
        The content to write.
    overwrite:
        When ``True`` an existing file is replaced atomically.  Defaults to
        ``False`` so that pre-existing files are preserved.

    Returns
    -------
    bool
        ``True`` when the file was freshly written; ``False`` when a
        pre-existing file was preserved.

    Raises
    ------
    SetupFilesError
        If *path* is a symlink (checked both before and after the write to
        guard against racing renames).
    """
    if path.is_symlink():
        msg = f"refusing to write env file: target is a symlink: {path}"
        raise SetupFilesError(msg)

    if path.exists() and not overwrite:
        return False  # preserve existing

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        encoded: bytes = content.encode()
        tmp_path: Path | None = None
        try:
            fd, tmp_str = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_env_")
            tmp_path = Path(tmp_str)
            try:
                if os.name == "posix":
                    # Restrict before writing so data is never visible to other users.
                    os.chmod(fd, 0o600)
                os.write(fd, encoded)
            finally:
                os.close(fd)

            # Race guard: re-check that the destination has not
            if path.is_symlink():
                msg = f"refusing to write env file: target became a symlink: {path}"
                raise SetupFilesError(msg)

            tmp_path.replace(path)
            tmp_path = None

        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    tmp_path.unlink()

        if os.name == "posix":
            path.chmod(0o600)
    except OSError as exc:
        raise SetupFilesError(f"failed to write env file {path}: {exc}") from exc

    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_setup(
    *,
    env_file: Path | str = DEFAULT_ENV_FILE,
    secrets_dir: Path | str = DEFAULT_SECRETS_DIR,
    signing_key_name: str = DEFAULT_SIGNING_KEY_NAME,
    force: bool = False,
) -> SetupResult:
    """Generate a ``.env`` file and an HMAC signing key for local setup.

    Both artifacts are preserved when they already exist — the function is
    fully idempotent by default.  Pass ``force=True`` to replace existing
    artifacts with freshly generated ones.

    A ``.gitignore`` file is created or updated in the same directory as
    *env_file* (the "setup root") to ensure that ``.env``, ``.env.*``, and
    ``.secrets/`` are ignored by git.  This update is idempotent — entries
    already present in the file are not duplicated.

    Parameters
    ----------
    env_file:
        Destination for the ``.env`` file.  Resolved to an absolute path at
        call time.  Defaults to ``.env`` in the current working directory.
    secrets_dir:
        Directory for private signing key material.  Created with mode
        ``0o700`` on POSIX when it does not exist.  Defaults to ``.secrets``
        in the current working directory.
    signing_key_name:
        Filename for the signing key inside *secrets_dir*.
    force:
        When ``True``, existing ``.env`` and signing key files are replaced
        with freshly generated content.  When ``False`` (the default),
        pre-existing files are preserved unchanged.

    Returns
    -------
    SetupResult
        Resolved paths and freshness flags for both artifacts.

    Raises
    ------
    SetupFilesError
        If the signing key path or the env file path is (or becomes) a
        symlink.
    """
    # Use .absolute() (not .resolve()) so that a symlink at the destination
    # is NOT followed.  .resolve() would silently follow the symlink chain and
    # deliver a non-symlink target path to write_secret / _write_env_idempotent,
    # bypassing their symlink guard.  .absolute() makes the path absolute by
    # prepending CWD when relative, without touching symlinks in any component.
    env_path = Path(env_file).expanduser().absolute()
    secrets_path = Path(secrets_dir).expanduser()
    # When secrets_dir is a relative default, anchor it to the setup root
    # (the directory containing env_file) so the secret stays with the project.
    if not secrets_path.is_absolute():
        secrets_path = env_path.parent / secrets_path
    key_path = (secrets_path / signing_key_name).absolute()

    # Capture pre-existing state *before* writing, so that the result flags
    # reflect what was on disk at call time rather than what we produced.
    # A symlink counts as "does not exist" because write_secret (or
    # _write_env_idempotent) will raise SetupFilesError before we return.
    key_existed = key_path.exists() and not key_path.is_symlink()

    # 1. Write signing key (atomic, private-mode).
    # When force=True an existing key is atomically replaced with a fresh one.
    write_secret(key_path, _generate_signing_key_material(), overwrite=force)

    # 2. Write .env (atomic, mode 0o600, no parent chmod).
    # When force=True an existing file is atomically replaced.
    # When the file exists and force=False, missing modal-mcp keys are
    # appended so that existing user configuration is preserved.
    fresh_env = _build_env_content(key_path)
    if env_path.exists() and not force:
        existing = env_path.read_text(encoding="utf-8")
        keys_to_add: list[str] = []
        for line in fresh_env.splitlines():
            if line.startswith("MODAL_MCP_"):
                key_name = line.split("=", 1)[0]
                if not _has_env_key(existing, key_name):
                    keys_to_add.append(line)
        if keys_to_add:
            merged = existing
            if not merged.endswith("\n"):
                merged += "\n"
            merged += "\n# Added by modal-mcp setup\n" + "\n".join(keys_to_add) + "\n"
            env_written = _write_env_idempotent(env_path, merged, overwrite=True)
        else:
            env_written = False
    else:
        env_written = _write_env_idempotent(env_path, fresh_env, overwrite=force)

    # 3. Update .gitignore in the setup root (env_file parent directory) so
    # that .env, .env.*, and .secrets/ are excluded from version control.
    # ensure_gitignore_entries is idempotent: already-present entries are
    # never duplicated.
    gitignore_path = env_path.parent / ".gitignore"
    gitignore_updated = ensure_gitignore_entries(gitignore_path)

    return SetupResult(
        # Use the absolute (non-symlink-following) paths for consistency.
        # Both writes succeeded (or the paths were preserved), so these paths
        # refer to real files on disk.
        env_file=env_path,
        signing_key_file=key_path,
        # When force=True we always generate/write fresh content, so the
        # "created" flags are True regardless of pre-existing state.
        signing_key_created=not key_existed or force,
        env_created=env_written,
        gitignore_updated=gitignore_updated,
    )


# ---------------------------------------------------------------------------
# Modal credential detection and warnings
# ---------------------------------------------------------------------------


def detect_modal_toml(
    modal_toml_path: Path | str | None = None,
) -> bool:
    """Return ``True`` when ``~/.modal.toml`` (or *modal_toml_path*) exists.

    Parameters
    ----------
    modal_toml_path:
        Override the path checked.  Defaults to ``~/.modal.toml``.  Pass an
        explicit path in tests to avoid touching the real home directory.
    """
    if modal_toml_path is None:
        path = Path.home() / ".modal.toml"
    else:
        path = Path(modal_toml_path).expanduser()
    return path.is_file()


def warn_if_modal_toml_present(
    *,
    modal_toml_path: Path | str | None = None,
    out: IO[str] | None = None,
) -> bool:
    """Print :data:`MODAL_TOML_CREDENTIAL_WARNING` if ``~/.modal.toml`` exists.

    Parameters
    ----------
    modal_toml_path:
        Override the path checked (for testing).  Defaults to ``~/.modal.toml``.
    out:
        Output stream for the warning.  Defaults to :data:`sys.stderr`.

    Returns
    -------
    bool
        ``True`` when the warning was printed (i.e. the file was found),
        ``False`` otherwise.
    """
    if not detect_modal_toml(modal_toml_path):
        return False
    stream = out if out is not None else sys.stderr
    print(MODAL_TOML_CREDENTIAL_WARNING, file=stream)
    return True


# ---------------------------------------------------------------------------
# TTY safety guard for credential choices
# ---------------------------------------------------------------------------


def require_tty_for_credential_choice(
    *,
    stdin: IO[str] | None = None,
) -> None:
    """Raise :exc:`CredentialChoiceError` when not running in an interactive TTY.

    Credential-choice prompts rely on interactive confirmation.  In a non-TTY
    environment (CI, piped input, scripted invocation) there is no human in the
    loop to confirm a choice, so the operation must be refused to prevent
    silent adoption of incorrect or unsafe credentials.

    Parameters
    ----------
    stdin:
        The input stream to check for TTY status.  Defaults to :data:`sys.stdin`.
        Pass a fake stream object in tests to exercise both branches.

    Raises
    ------
    CredentialChoiceError
        When *stdin* is not connected to a terminal.
    """
    stream = stdin if stdin is not None else sys.stdin
    is_tty = hasattr(stream, "isatty") and stream.isatty()
    if not is_tty:
        raise CredentialChoiceError(
            "Credential choices require an interactive terminal (TTY). "
            "Non-TTY setup refuses credential choices without explicit safe input. "
            "Provide credentials explicitly or run in an interactive shell."
        )


# ---------------------------------------------------------------------------
# File-backed service-user token storage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServiceTokenResult:
    """Paths to written (or preserved) file-backed service-user token files."""

    #: Absolute path to the token-ID file.
    token_id_file: Path
    #: Absolute path to the token-secret file.
    token_secret_file: Path
    #: ``True`` when the token-ID file was freshly written.
    token_id_created: bool
    #: ``True`` when the token-secret file was freshly written.
    token_secret_created: bool


def write_service_token_files(
    *,
    token_id: str,
    token_secret: str,
    secrets_dir: Path | str = DEFAULT_SECRETS_DIR,
    token_id_name: str = DEFAULT_TOKEN_ID_NAME,
    token_secret_name: str = DEFAULT_TOKEN_SECRET_NAME,
) -> ServiceTokenResult:
    """Write service-user Modal credentials as file-backed secrets.

    Credential material is written to private files (mode ``0o600``) inside a
    private directory (mode ``0o700``).  Credentials are **never** written to
    environment variables or ``.env`` files.

    Both files are preserved when they already exist — the function is
    idempotent.  Symlink targets are refused for both files.

    Parameters
    ----------
    token_id:
        The Modal token ID (``MODAL_TOKEN_ID`` value).
    token_secret:
        The Modal token secret (``MODAL_TOKEN_SECRET`` value).
    secrets_dir:
        Directory for the private token files.  Created with mode ``0o700``
        when it does not exist.  Defaults to ``.secrets`` in the current
        working directory.
    token_id_name:
        Filename for the token-ID file inside *secrets_dir*.
    token_secret_name:
        Filename for the token-secret file inside *secrets_dir*.

    Returns
    -------
    ServiceTokenResult
        Resolved paths and freshness flags for both files.

    Raises
    ------
    SetupFilesError
        If either file path is (or becomes) a symlink, or if *secrets_dir* is
        a symlink.
    """
    secrets_path = Path(secrets_dir).expanduser().absolute()
    id_path = (secrets_path / token_id_name).absolute()
    secret_path = (secrets_path / token_secret_name).absolute()

    # Capture pre-existing state before writing.
    id_existed = id_path.exists() and not id_path.is_symlink()
    secret_existed = secret_path.exists() and not secret_path.is_symlink()

    # write_secret is atomic, idempotent (overwrite=False), and enforces
    # 0o600 on the file and 0o700 on the parent directory.
    write_secret(id_path, token_id)
    write_secret(secret_path, token_secret)

    return ServiceTokenResult(
        token_id_file=id_path,
        token_secret_file=secret_path,
        token_id_created=not id_existed,
        token_secret_created=not secret_existed,
    )


# ---------------------------------------------------------------------------
# Output redaction helper
# ---------------------------------------------------------------------------

#: Minimum character length for a value to be treated as a redactable secret.
#: Must stay in sync with :attr:`modal_mcp.observability.redact.MIN_SECRET_LENGTH`.
_MIN_SECRET_LENGTH: int = 4


def collect_setup_known_secrets(result: SetupResult) -> frozenset[str]:
    """Return signing-key material from *result* for output-line redaction.

    Reads the signing key file referenced by *result* and returns both the
    full ``kid:hex`` string and the bare hex tail as a :class:`frozenset`.
    Pass the returned value as ``known_secrets`` to
    :func:`~modal_mcp.observability.redact.redact_string` when formatting
    setup output so that key material is never accidentally echoed.

    Returns an empty :class:`frozenset` when the key file cannot be read (e.g.
    the file was removed between :func:`run_setup` returning and this call).
    """
    values: set[str] = set()
    try:
        raw = result.signing_key_file.read_text(encoding="utf-8").strip()
    except OSError:
        return frozenset()
    if raw and len(raw) >= _MIN_SECRET_LENGTH:
        values.add(raw)
        if ":" in raw:
            _, _, hex_part = raw.partition(":")
            if len(hex_part) >= _MIN_SECRET_LENGTH:
                values.add(hex_part)
    return frozenset(values)


__all__ = [
    "DEFAULT_ENV_FILE",
    "DEFAULT_SECRETS_DIR",
    "DEFAULT_SIGNING_KEY_NAME",
    "DEFAULT_TOKEN_ID_NAME",
    "DEFAULT_TOKEN_SECRET_NAME",
    "FORBIDDEN_SETUP_ENV_KEYS",
    "MODAL_TOML_CREDENTIAL_WARNING",
    "CredentialChoiceError",
    "ServiceTokenResult",
    "SetupResult",
    "collect_setup_known_secrets",
    "detect_modal_toml",
    "require_tty_for_credential_choice",
    "run_setup",
    "warn_if_modal_toml_present",
    "write_service_token_files",
]
