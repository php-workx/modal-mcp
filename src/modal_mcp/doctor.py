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
- SDK auth health is probed via :func:`_probe_modal_auth` when credentials are
  present; SDK *import* success alone is not treated as auth success.
- Read-only readiness is reported via ``MODAL_MCP_READ_ONLY`` and
  ``MODAL_MCP_ENABLED_TOOLSETS`` checks drawn from env vars or the selected
  ``.env`` file.
- Modal CLI presence is reported as a separate, standalone item.
"""

from __future__ import annotations

import enum
import os
import shutil
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

#: Minimum character length for a value to be treated as a redactable secret.
#: Must stay in sync with :attr:`modal_mcp.observability.redact.MIN_SECRET_LENGTH`.
_MIN_SECRET_LENGTH: int = 4

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
        """Process exit code reflecting the overall diagnostic state.

        Returns
        -------
        int
            ``1`` when any check fails, ``3`` when warnings exist without
            failures (partial-ready state), ``0`` when all checks pass.
        """
        if self.has_failures:
            return 1
        if self.has_warnings:
            return 3
        return 0


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


def _import_modal_mcp() -> None:
    import modal_mcp as imported

    del imported


def _import_modal() -> None:
    import modal as imported

    del imported


def _import_fastmcp() -> None:
    import fastmcp as imported

    del imported


def _import_uvicorn() -> None:
    import uvicorn as imported

    del imported


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into a ``{key: value}`` dict.

    The file is read without modifying ``os.environ``.  Lines beginning with
    ``#`` and blank lines are ignored.  An optional ``export `` prefix (as
    written by some shell-compatible dotenv files) is stripped before parsing.
    Surrounding single or double quotes on values are stripped.

    Raises :class:`OSError` or :class:`UnicodeError` if the file cannot be read.
    """
    result: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Handle shell-style "export KEY=value" prefix.
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
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
# Output redaction helpers
# ---------------------------------------------------------------------------


def _collect_output_known_secrets(
    env_file_vars: dict[str, str] | None = None,
) -> frozenset[str]:
    """Collect secret values for diagnostic-message redaction.

    Returns values of ``MODAL_TOKEN_ID``, ``MODAL_TOKEN_SECRET``,
    ``MODAL_MCP_SIGNING_KEYS``, and the contents of any referenced token or
    signing-key files, sourced from both ``os.environ`` and *env_file_vars*
    (when provided).

    For signing keys in ``kid:hex`` format the bare hex tail is added as a
    separate entry so that it is redacted even when printed without the prefix.
    """
    _file_vars: dict[str, str] = env_file_vars if env_file_vars is not None else {}
    values: set[str] = set()

    def _add(val: str) -> None:
        if val and len(val) >= _MIN_SECRET_LENGTH:
            values.add(val)
            if ":" in val:
                _, _, tail = val.partition(":")
                if len(tail) >= _MIN_SECRET_LENGTH:
                    values.add(tail)

    # Inline secret env vars.
    for key in ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "MODAL_MCP_SIGNING_KEYS"):
        _add(os.environ.get(key, ""))
        _add(_file_vars.get(key, ""))

    # File-backed token secrets: read and redact the file contents.
    for file_key in ("MODAL_TOKEN_ID_FILE", "MODAL_TOKEN_SECRET_FILE"):
        for src in (os.environ, _file_vars):
            file_path_str = src.get(file_key, "")
            if file_path_str:
                try:
                    content = (
                        Path(file_path_str)
                        .expanduser()
                        .read_text(encoding="utf-8")
                        .strip()
                    )
                    _add(content)
                except (OSError, UnicodeDecodeError):
                    pass

    # File-backed signing key: read and redact the file contents.
    for src in (os.environ, _file_vars):
        key_file_str = src.get("MODAL_MCP_SIGNING_KEY_FILE", "")
        if key_file_str:
            try:
                content = (
                    Path(key_file_str).expanduser().read_text(encoding="utf-8").strip()
                )
                _add(content)
            except (OSError, UnicodeDecodeError):
                pass

    return frozenset(values)


# ---------------------------------------------------------------------------
# Modal SDK auth probe
# ---------------------------------------------------------------------------


def _read_secret_file_for_sdk(path_str: str, *, env_var_name: str) -> str:
    """Read a file-backed Modal token for the SDK auth probe."""
    path = Path(path_str).expanduser()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        msg = f"{env_var_name} points to unreadable file {path}: {exc}"
        raise RuntimeError(msg) from exc
    if not value:
        msg = f"{env_var_name} points to empty file {path}"
        raise RuntimeError(msg)
    return value


def _modal_sdk_env_overrides(
    env_file_vars: Mapping[str, str] | None,
    *,
    modal_config_path: Path | None = None,
) -> dict[str, str]:
    """Return temporary env vars that make Modal SDK auth mirror doctor inputs."""
    env_file_vars = env_file_vars or {}
    overrides: dict[str, str] = {}

    token_id = os.environ.get("MODAL_TOKEN_ID") or env_file_vars.get("MODAL_TOKEN_ID")
    token_secret = os.environ.get("MODAL_TOKEN_SECRET") or env_file_vars.get(
        "MODAL_TOKEN_SECRET"
    )

    if not token_id:
        token_id_file = os.environ.get("MODAL_TOKEN_ID_FILE") or env_file_vars.get(
            "MODAL_TOKEN_ID_FILE"
        )
        if token_id_file:
            token_id = _read_secret_file_for_sdk(
                token_id_file, env_var_name="MODAL_TOKEN_ID_FILE"
            )

    if not token_secret:
        token_secret_file = os.environ.get(
            "MODAL_TOKEN_SECRET_FILE"
        ) or env_file_vars.get("MODAL_TOKEN_SECRET_FILE")
        if token_secret_file:
            token_secret = _read_secret_file_for_sdk(
                token_secret_file, env_var_name="MODAL_TOKEN_SECRET_FILE"
            )

    if token_id and "MODAL_TOKEN_ID" not in os.environ:
        overrides["MODAL_TOKEN_ID"] = token_id
    if token_secret and "MODAL_TOKEN_SECRET" not in os.environ:
        overrides["MODAL_TOKEN_SECRET"] = token_secret
    if modal_config_path is not None:
        overrides["MODAL_CONFIG_PATH"] = str(modal_config_path)

    return overrides


@contextmanager
def _temporary_env(overrides: Mapping[str, str]) -> Iterator[None]:
    """Temporarily set environment variables and restore the prior process state."""
    prior: dict[str, str | None] = {key: os.environ.get(key) for key in overrides}
    try:
        for key, override_value in overrides.items():
            os.environ[key] = override_value
        yield
    finally:
        for key, prior_value in prior.items():
            if prior_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior_value


def _probe_modal_auth(
    env_file_vars: Mapping[str, str] | None = None,
    *,
    modal_config_path: Path | None = None,
) -> None:
    """Probe Modal SDK credential health without making a network call.

    Uses :class:`modal.config.Config` to verify that both ``token_id`` and
    ``token_secret`` are resolvable via the SDK's own credential resolution.
    Values discovered in the selected ``.env`` file or file-backed token files
    are exposed only through temporary process environment overrides for the
    duration of the probe.  Raises
    :class:`Exception` on any failure — import error, missing config entry,
    or empty credential value.

    This distinguishes *SDK importability* (modal package installed) from
    *auth health* (Modal SDK can actually locate valid-looking credentials).
    """
    from modal.config import Config

    overrides = _modal_sdk_env_overrides(
        env_file_vars, modal_config_path=modal_config_path
    )
    with _temporary_env(overrides):
        cfg = Config()  # type: ignore[no-untyped-call]
        token_id = cfg["token_id"]
        token_secret = cfg["token_secret"]
    if not token_id or not token_secret:
        raise RuntimeError(
            "Modal SDK configuration: token_id or token_secret not found"
        )


# ---------------------------------------------------------------------------
# Public credential probe
# ---------------------------------------------------------------------------


def probe_credentials(
    env_file: Path | None = None,
    modal_config_path: Path | None = None,
    env_file_vars: Mapping[str, str] | None = None,
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
    env_file_vars:
        Already parsed dotenv values.  Supplying this avoids a second file read
        after :func:`run_doctor` has already validated the selected file.

    Returns
    -------
    CredentialProbeResult
        ``found=True`` on the first source that provides credentials;
        ``found=False`` when no source yields credentials.
    """
    parsed_env_file_vars: Mapping[str, str] = env_file_vars or {}
    if env_file_vars is None and env_file is not None and env_file.is_file():
        parsed_env_file_vars = _parse_env_file(env_file)

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

    if parsed_env_file_vars:
        # 3. Direct token pair in .env file.
        if parsed_env_file_vars.get("MODAL_TOKEN_ID") and parsed_env_file_vars.get(
            "MODAL_TOKEN_SECRET"
        ):
            return CredentialProbeResult(
                found=True,
                source="env_file",
                detail=f"MODAL_TOKEN_ID + MODAL_TOKEN_SECRET in {env_file}",
            )

        # 4. File-backed token pair referenced in .env file.
        id_file_env = parsed_env_file_vars.get("MODAL_TOKEN_ID_FILE")
        secret_file_env = parsed_env_file_vars.get("MODAL_TOKEN_SECRET_FILE")
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
        or (
            parsed_env_file_vars.get("MODAL_CONFIG_PATH")
            if parsed_env_file_vars
            else None
        )
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


#: Values interpreted as boolean ``True`` for ``MODAL_MCP_READ_ONLY``.
_READ_ONLY_ENABLED_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})

#: Toolset names that can perform write / mutating operations.
_MUTATING_TOOLSETS: frozenset[str] = frozenset({"change", "expert"})


def _check_read_only(env_file_vars: dict[str, str]) -> DiagnosticItem:
    """Report whether MODAL_MCP_READ_ONLY is configured for safe read-only use.

    Severity levels:

    - **OK**: the variable is absent (defaults to ``true``) or explicitly set
      to a truthy value.
    - **WARN**: the variable is explicitly set to a falsy value — the server
      will accept write operations from agents.
    """
    value = _resolve_env_var("MODAL_MCP_READ_ONLY", env_file_vars)
    if value is None:
        return DiagnosticItem(
            "read_only",
            CheckStatus.OK,
            "MODAL_MCP_READ_ONLY not set — defaults to true (read-only mode enabled)",
        )
    if value.lower() in _READ_ONLY_ENABLED_VALUES:
        return DiagnosticItem(
            "read_only",
            CheckStatus.OK,
            f"MODAL_MCP_READ_ONLY={value!r}: read-only mode enabled",
        )
    return DiagnosticItem(
        "read_only",
        CheckStatus.WARN,
        f"MODAL_MCP_READ_ONLY={value!r}: read-only mode is disabled"
        " — the server will accept write operations from agents",
    )


def _check_toolsets(env_file_vars: dict[str, str]) -> DiagnosticItem:
    """Report whether MODAL_MCP_ENABLED_TOOLSETS includes mutating toolsets.

    Severity levels:

    - **OK**: the variable is absent (default toolsets are all read-only) or
      set to a list that contains no ``change`` or ``expert`` toolset names.
    - **WARN**: ``change`` and/or ``expert`` toolsets are enabled — these can
      perform write operations and represent a read-only readiness risk.
    """
    toolsets_value = _resolve_env_var("MODAL_MCP_ENABLED_TOOLSETS", env_file_vars)
    if toolsets_value is None:
        return DiagnosticItem(
            "toolsets",
            CheckStatus.OK,
            "MODAL_MCP_ENABLED_TOOLSETS not set — default toolsets are read-only",
        )
    enabled = {t.strip().lower() for t in toolsets_value.split(",") if t.strip()}
    risky = sorted(enabled & _MUTATING_TOOLSETS)
    if risky:
        return DiagnosticItem(
            "toolsets",
            CheckStatus.WARN,
            f"MODAL_MCP_ENABLED_TOOLSETS includes mutating toolsets: {risky!r}"
            " — these can perform write operations;"
            " ensure MODAL_MCP_READ_ONLY=true to restrict them",
        )
    return DiagnosticItem(
        "toolsets",
        CheckStatus.OK,
        f"MODAL_MCP_ENABLED_TOOLSETS={toolsets_value!r}: no mutating toolsets enabled",
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
    5. Read-only readiness (``MODAL_MCP_READ_ONLY``).
    6. Enabled toolsets (``MODAL_MCP_ENABLED_TOOLSETS`` — warns on ``change``
       or ``expert``).
    7. Modal credential probe (env vars, ``.env``, file-backed paths,
       ``~/.modal.toml``).
    8. SDK auth health — **authoritative** when credentials are present (calls
       :func:`_probe_modal_auth`); reported as a warning (skipped) when they
       are absent.
    9. Modal CLI presence (reported separately from SDK checks).

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
        the suggested process exit code (``0`` = all OK, ``3`` = partial-ready
        with warnings, ``1`` = failures present).
    """
    report = DiagnosticReport()
    actual_env_file = env_file if env_file is not None else Path(".env")

    # ------------------------------------------------------------------
    # 1. Package import checks
    # ------------------------------------------------------------------
    # modal SDK is a client library; the server can start in a degraded state
    # without it being locally importable (e.g. in environments where modal
    # credentials are managed externally).  Absence is therefore non-fatal.
    _PACKAGE_CHECKS: list[tuple[str, str, CheckStatus, Callable[[], None]]] = [
        ("modal_mcp", "modal-mcp package", CheckStatus.FAIL, _import_modal_mcp),
        ("modal", "modal SDK", CheckStatus.WARN, _import_modal),
        ("fastmcp", "fastmcp", CheckStatus.FAIL, _import_fastmcp),
        ("uvicorn", "uvicorn", CheckStatus.FAIL, _import_uvicorn),
    ]
    for module_name, label, missing_status, import_module in _PACKAGE_CHECKS:
        try:
            import_module()
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
                    missing_status,
                    f"{label} not importable: {exc}",
                )
            )

    # ------------------------------------------------------------------
    # 2. .env file
    # ------------------------------------------------------------------
    env_file_is_usable = False
    if actual_env_file.is_file():
        try:
            env_file_vars = _parse_env_file(actual_env_file)
        except (OSError, UnicodeError) as exc:
            report.items.append(
                DiagnosticItem(
                    "env_file",
                    CheckStatus.FAIL,
                    f"env file cannot be read: {actual_env_file}: {exc}",
                )
            )
            env_file_vars = {}
        else:
            report.items.append(
                DiagnosticItem(
                    "env_file",
                    CheckStatus.OK,
                    f"env file found: {actual_env_file}",
                )
            )
            env_file_is_usable = True
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
    # 5. Read-only readiness
    # ------------------------------------------------------------------
    report.items.append(_check_read_only(env_file_vars))

    # ------------------------------------------------------------------
    # 6. Enabled toolsets
    # ------------------------------------------------------------------
    report.items.append(_check_toolsets(env_file_vars))

    # ------------------------------------------------------------------
    # 7. Modal credential probe
    # ------------------------------------------------------------------
    cred = probe_credentials(
        env_file=actual_env_file if env_file_is_usable else None,
        modal_config_path=modal_config_path,
        env_file_vars=env_file_vars if env_file_is_usable else None,
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
    # 8. SDK auth (authoritative when credentials are present)
    # ------------------------------------------------------------------
    if cred.found:
        modal_importable = any(
            i.name == "import:modal" and i.status == CheckStatus.OK
            for i in report.items
        )
        if modal_importable:
            try:
                _probe_modal_auth(
                    env_file_vars if env_file_is_usable else None,
                    modal_config_path=modal_config_path,
                )
                report.items.append(
                    DiagnosticItem(
                        "sdk_auth",
                        CheckStatus.OK,
                        "Modal SDK credential probe passed",
                    )
                )
            except Exception as exc:
                report.items.append(
                    DiagnosticItem(
                        "sdk_auth",
                        CheckStatus.FAIL,
                        f"Modal SDK auth probe failed: {exc}",
                    )
                )
        else:
            report.items.append(
                DiagnosticItem(
                    "sdk_auth",
                    CheckStatus.FAIL,
                    "Modal SDK not importable; cannot validate credentials",
                )
            )
    else:
        report.items.append(
            DiagnosticItem(
                "sdk_auth",
                CheckStatus.WARN,
                "SDK auth check skipped: no credentials found",
            )
        )

    # ------------------------------------------------------------------
    # 9. Modal CLI (reported separately from SDK checks)
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

    # ------------------------------------------------------------------
    # Redact sensitive values from all diagnostic messages.
    # The import is deferred so that loading doctor does not trigger full
    # Settings validation (redact.py transitively imports config.Settings
    # at module level, which is safe once we are inside a function call).
    # ------------------------------------------------------------------
    known = _collect_output_known_secrets(env_file_vars)
    if known:
        from modal_mcp.observability.redact import redact_string

        report.items = [
            DiagnosticItem(
                item.name,
                item.status,
                redact_string(item.message, known_secrets=known),
            )
            for item in report.items
        ]

    return report


__all__ = [
    "CheckStatus",
    "CredentialProbeResult",
    "DiagnosticItem",
    "DiagnosticReport",
    "probe_credentials",
    "run_doctor",
]
