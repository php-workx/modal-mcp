"""Partial-loading diagnostic checks for modal-mcp.

``doctor`` is intentionally designed to work *before* the full runtime
``Settings`` can be validated.  Its job is to explain missing ``.env`` files,
missing signing keys, and absent Modal credentials with actionable messages
rather than opaque startup errors.

Design constraints
------------------
- No import of :class:`~modal_mcp.config.Settings` anywhere in this module.
- ``probe_credentials`` discovers credentials from four independent sources
  (environment variables, a ``.env`` file, file-backed token paths, and
  ``~/.modal.toml``) without loading anything into ``os.environ``.
- SDK import checks are **authoritative** only when credentials are present;
  when credentials are absent the check is downgraded to informational.
- Modal CLI presence is reported as a separate, standalone item.
"""

from __future__ import annotations

import enum
import importlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Status levels
# ---------------------------------------------------------------------------


class CheckStatus(enum.Enum):
    """Severity level of a single diagnostic item."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiagnosticItem:
    """A single named check with its outcome and a human-readable message."""

    name: str
    status: CheckStatus
    message: str


@dataclass
class DiagnosticReport:
    """Ordered collection of :class:`DiagnosticItem` results."""

    items: list[DiagnosticItem] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        """``True`` when at least one item has ``FAIL`` status."""
        return any(i.status == CheckStatus.FAIL for i in self.items)

    @property
    def has_warnings(self) -> bool:
        """``True`` when at least one item has ``WARN`` status."""
        return any(i.status == CheckStatus.WARN for i in self.items)

    @property
    def exit_code(self) -> int:
        """``1`` when there are failures, ``0`` otherwise (warnings are non-fatal)."""
        return 1 if self.has_failures else 0


@dataclass(frozen=True, slots=True)
class CredentialProbeResult:
    """Outcome of :func:`probe_credentials`."""

    found: bool
    #: One of ``"environ"``, ``"env_file"``, ``"file_backed"``,
    #: ``"modal_toml"``, or ``"none"``.
    source: str
    detail: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into a ``{key: value}`` dict.

    The file is read without modifying ``os.environ``.  Lines beginning with
    ``#`` and blank lines are ignored.  Surrounding single or double quotes on
    values are stripped.

    Returns an empty dict if the file cannot be read.
    """
    result: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return result

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip balanced surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            result[key] = value

    return result


def _resolve_env_var(key: str, env_file_vars: dict[str, str]) -> str | None:
    """Return the first non-empty value found in ``os.environ`` then *env_file_vars*."""
    return os.environ.get(key) or env_file_vars.get(key) or None


# ---------------------------------------------------------------------------
# Public credential probe
# ---------------------------------------------------------------------------


def probe_credentials(
    env_file: Path | None = None,
    modal_config_path: Path | None = None,
) -> CredentialProbeResult:
    """Discover Modal credentials without requiring full ``Settings`` validation.

    Sources checked in priority order:

    1. Direct ``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET`` in ``os.environ``.
    2. File-backed ``MODAL_TOKEN_ID_FILE`` / ``MODAL_TOKEN_SECRET_FILE`` from
       ``os.environ``.
    3. The same direct and file-backed variables found in *env_file* (parsed
       without modifying ``os.environ``).
    4. ``~/.modal.toml`` (or the path specified by ``MODAL_CONFIG_PATH`` /
       *modal_config_path*).

    Parameters
    ----------
    env_file:
        Path to a ``.env`` file to inspect.  If ``None`` or the path does not
        exist the file-based checks are skipped.
    modal_config_path:
        Override for the Modal config file path.  Defaults to the path given
        by ``MODAL_CONFIG_PATH`` in the environment, or ``~/.modal.toml``.

    Returns
    -------
    CredentialProbeResult
        ``found=True`` on the first source that provides credentials;
        ``found=False`` when no source yields credentials.
    """
    env_file_vars: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        env_file_vars = _parse_env_file(env_file)

    # 1. Direct token pair in os.environ.
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return CredentialProbeResult(
            found=True,
            source="environ",
            detail="MODAL_TOKEN_ID + MODAL_TOKEN_SECRET in environment",
        )

    # 2. File-backed token pair from os.environ.
    id_file_str = os.environ.get("MODAL_TOKEN_ID_FILE")
    secret_file_str = os.environ.get("MODAL_TOKEN_SECRET_FILE")
    if id_file_str and secret_file_str:
        id_path = Path(id_file_str).expanduser()
        secret_path = Path(secret_file_str).expanduser()
        if id_path.is_file() and secret_path.is_file():
            return CredentialProbeResult(
                found=True,
                source="file_backed",
                detail=f"token files: {id_file_str}, {secret_file_str}",
            )

    if env_file_vars:
        # 3. Direct token pair in .env file.
        if env_file_vars.get("MODAL_TOKEN_ID") and env_file_vars.get(
            "MODAL_TOKEN_SECRET"
        ):
            return CredentialProbeResult(
                found=True,
                source="env_file",
                detail=f"MODAL_TOKEN_ID + MODAL_TOKEN_SECRET in {env_file}",
            )

        # 4. File-backed token pair referenced in .env file.
        id_file_env = env_file_vars.get("MODAL_TOKEN_ID_FILE")
        secret_file_env = env_file_vars.get("MODAL_TOKEN_SECRET_FILE")
        if id_file_env and secret_file_env:
            id_path = Path(id_file_env).expanduser()
            secret_path = Path(secret_file_env).expanduser()
            if id_path.is_file() and secret_path.is_file():
                return CredentialProbeResult(
                    found=True,
                    source="file_backed",
                    detail=(
                        f"token files referenced in {env_file}:"
                        f" {id_file_env}, {secret_file_env}"
                    ),
                )

    # 5. ~/.modal.toml (or override).
    config_path_str = (
        os.environ.get("MODAL_CONFIG_PATH")
        or (env_file_vars.get("MODAL_CONFIG_PATH") if env_file_vars else None)
        or "~/.modal.toml"
    )
    effective_config_path = (
        modal_config_path
        if modal_config_path is not None
        else Path(config_path_str).expanduser()
    )
    if effective_config_path.is_file():
        return CredentialProbeResult(
            found=True,
            source="modal_toml",
            detail=str(effective_config_path),
        )

    return CredentialProbeResult(found=False, source="none", detail="")


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------


def _check_signing_key(env_file_vars: dict[str, str]) -> DiagnosticItem:
    """Report whether a signing key is configured.

    Severity levels:

    - **OK**: key material is present (inline or via a file that exists).
    - **FAIL**: key file path is explicitly configured but the file is missing
      (misconfiguration that cannot succeed at startup).
    - **WARN**: no signing key source is configured at all (user needs to run
      ``setup --yes`` or set the variable manually).
    """
    if _resolve_env_var("MODAL_MCP_SIGNING_KEYS", env_file_vars):
        return DiagnosticItem(
            "signing_key",
            CheckStatus.OK,
            "signing key configured via MODAL_MCP_SIGNING_KEYS",
        )

    key_file_str = _resolve_env_var("MODAL_MCP_SIGNING_KEY_FILE", env_file_vars)
    if key_file_str:
        key_path = Path(key_file_str).expanduser()
        if key_path.is_file():
            return DiagnosticItem(
                "signing_key",
                CheckStatus.OK,
                f"signing key file present: {key_file_str}",
            )
        return DiagnosticItem(
            "signing_key",
            CheckStatus.FAIL,
            f"MODAL_MCP_SIGNING_KEY_FILE points to missing file: {key_file_str}",
        )

    return DiagnosticItem(
        "signing_key",
        CheckStatus.WARN,
        "no signing key configured"
        " — set MODAL_MCP_SIGNING_KEYS or MODAL_MCP_SIGNING_KEY_FILE",
    )


def _check_allowed_origins(env_file_vars: dict[str, str]) -> DiagnosticItem:
    """Report whether MODAL_MCP_ALLOWED_ORIGINS is configured."""
    origins = _resolve_env_var("MODAL_MCP_ALLOWED_ORIGINS", env_file_vars)
    if origins and origins.strip():
        return DiagnosticItem(
            "allowed_origins",
            CheckStatus.OK,
            f"MODAL_MCP_ALLOWED_ORIGINS: {origins}",
        )
    return DiagnosticItem(
        "allowed_origins",
        CheckStatus.WARN,
        "MODAL_MCP_ALLOWED_ORIGINS not set"
        " — the server will refuse to start without it",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_doctor(
    env_file: Path | None = None,
    modal_config_path: Path | None = None,
) -> DiagnosticReport:
    """Run all diagnostic checks without requiring full ``Settings`` validation.

    Checks performed (in order):

    1. Package import checks: ``modal_mcp``, ``modal``, ``fastmcp``,
       ``uvicorn``.
    2. ``.env`` file presence.
    3. Signing key configuration (``MODAL_MCP_SIGNING_KEYS`` or
       ``MODAL_MCP_SIGNING_KEY_FILE``).
    4. Allowed origins (``MODAL_MCP_ALLOWED_ORIGINS``).
    5. Modal credential probe (env vars, ``.env``, file-backed paths,
       ``~/.modal.toml``).
    6. SDK auth health — **authoritative** when credentials are present;
       reported as a warning (skipped) when they are absent.
    7. Modal CLI presence (reported separately from SDK checks).

    Parameters
    ----------
    env_file:
        Path to a ``.env`` file to probe.  Defaults to ``Path(".env")``
        (resolved relative to the process CWD at call time).
    modal_config_path:
        Override for the Modal config file path passed to
        :func:`probe_credentials`.

    Returns
    -------
    DiagnosticReport
        All diagnostic items; inspect :attr:`DiagnosticReport.exit_code` for
        the suggested process exit code.
    """
    report = DiagnosticReport()
    actual_env_file = env_file if env_file is not None else Path(".env")

    # ------------------------------------------------------------------
    # 1. Package import checks
    # ------------------------------------------------------------------
    _PACKAGE_CHECKS: list[tuple[str, str]] = [
        ("modal_mcp", "modal-mcp package"),
        ("modal", "modal SDK"),
        ("fastmcp", "fastmcp"),
        ("uvicorn", "uvicorn"),
    ]
    for module_name, label in _PACKAGE_CHECKS:
        try:
            importlib.import_module(module_name)
            report.items.append(
                DiagnosticItem(
                    f"import:{module_name}",
                    CheckStatus.OK,
                    f"{label} importable",
                )
            )
        except ImportError as exc:
            report.items.append(
                DiagnosticItem(
                    f"import:{module_name}",
                    CheckStatus.FAIL,
                    f"{label} not importable: {exc}",
                )
            )

    # ------------------------------------------------------------------
    # 2. .env file
    # ------------------------------------------------------------------
    if actual_env_file.is_file():
        report.items.append(
            DiagnosticItem(
                "env_file",
                CheckStatus.OK,
                f"env file found: {actual_env_file}",
            )
        )
        env_file_vars = _parse_env_file(actual_env_file)
    else:
        report.items.append(
            DiagnosticItem(
                "env_file",
                CheckStatus.WARN,
                f"env file not found: {actual_env_file} — run 'modal-mcp setup --yes'",
            )
        )
        env_file_vars = {}

    # ------------------------------------------------------------------
    # 3. Signing key
    # ------------------------------------------------------------------
    report.items.append(_check_signing_key(env_file_vars))

    # ------------------------------------------------------------------
    # 4. Allowed origins
    # ------------------------------------------------------------------
    report.items.append(_check_allowed_origins(env_file_vars))

    # ------------------------------------------------------------------
    # 5. Modal credential probe
    # ------------------------------------------------------------------
    cred = probe_credentials(
        env_file=actual_env_file if actual_env_file.is_file() else None,
        modal_config_path=modal_config_path,
    )
    if cred.found:
        report.items.append(
            DiagnosticItem(
                "credentials",
                CheckStatus.OK,
                f"Modal credentials found ({cred.source}): {cred.detail}",
            )
        )
    else:
        report.items.append(
            DiagnosticItem(
                "credentials",
                CheckStatus.WARN,
                "Modal credentials not found"
                " — add MODAL_TOKEN_ID/SECRET or configure ~/.modal.toml",
            )
        )

    # ------------------------------------------------------------------
    # 6. SDK auth (authoritative when credentials are present)
    # ------------------------------------------------------------------
    if cred.found:
        modal_importable = any(
            i.name == "import:modal" and i.status == CheckStatus.OK
            for i in report.items
        )
        if modal_importable:
            report.items.append(
                DiagnosticItem(
                    "sdk_auth",
                    CheckStatus.OK,
                    "modal SDK importable with credentials present",
                )
            )
        # else: import:modal already recorded a FAIL; no additional sdk_auth item
    else:
        report.items.append(
            DiagnosticItem(
                "sdk_auth",
                CheckStatus.WARN,
                "SDK auth check skipped: no credentials found",
            )
        )

    # ------------------------------------------------------------------
    # 7. Modal CLI (reported separately from SDK checks)
    # ------------------------------------------------------------------
    cli_path = shutil.which("modal")
    if cli_path:
        report.items.append(
            DiagnosticItem(
                "modal_cli",
                CheckStatus.OK,
                f"modal CLI found: {cli_path}",
            )
        )
    else:
        report.items.append(
            DiagnosticItem(
                "modal_cli",
                CheckStatus.WARN,
                "modal CLI not found in PATH (optional for server operation)",
            )
        )

    return report


__all__ = [
    "CheckStatus",
    "CredentialProbeResult",
    "DiagnosticItem",
    "DiagnosticReport",
    "probe_credentials",
    "run_doctor",
]
