"""Modal credential resolution with explicit provenance.

This module owns the *resolution* phase of bootstrap.  It is pure:
no Modal SDK import, no network I/O.  Failures raise CredentialError
with messages that name the source ('env var X', 'TOML file Y at profile Z').
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from modal_mcp.config import Settings

CredentialSourceKind = Literal["env", "toml", "injected"]


class CredentialError(ValueError):
    """Raised when Modal credentials cannot be resolved from any source."""


@dataclass(frozen=True, slots=True)
class ModalCredentials:
    """Resolved Modal credentials with explicit provenance.

    Attributes
    ----------
    token_id:
        Modal API token id, wrapped in :class:`SecretStr` so accidental
        logging does not leak the value.
    token_secret:
        Modal API token secret, wrapped in :class:`SecretStr`.
    source:
        One of ``"env"``, ``"toml"``, or ``"injected"``.  Drives the
        operator-facing ``describe()`` message used by ``doctor`` and
        bootstrap failure reporting.
    profile:
        TOML profile name when ``source == "toml"``; ``None`` otherwise.
    config_path:
        Absolute path to the modal.toml file when ``source == "toml"``;
        ``None`` otherwise.
    """

    token_id: SecretStr
    token_secret: SecretStr
    source: CredentialSourceKind
    profile: str | None = None
    config_path: Path | None = field(default=None)

    def describe(self) -> str:
        """Return an operator-facing provenance string (no secret material)."""
        if self.source == "env":
            return "loaded from MODAL_TOKEN_ID env var"
        if self.source == "toml":
            path = self.config_path or Path("~/.modal.toml")
            profile = self.profile or "default"
            return f"loaded from {path} at profile '{profile}'"
        return "injected by caller (test/fake)"


class CredentialSource:
    """Resolve Modal credentials from Settings with explicit provenance.

    The class is intentionally a namespace (single classmethod) rather than
    an instance: there is no resolver state to thread, and the call site
    reads better as ``CredentialSource.resolve(settings)``.
    """

    @classmethod
    def resolve(cls, settings: Settings) -> ModalCredentials:
        """Resolve credentials with explicit provenance.

        Priority: (1) ``settings.modal_token_id`` + ``modal_token_secret``
        (includes file-backed ``*_FILE`` per ``Settings._load_file_backed_secrets``)
        -> source ``"env"``.  (2) ``settings.modal_config_path`` (default
        ``~/.modal.toml``) with ``settings.modal_profile`` (default ``"default"``)
        -> source ``"toml"``.  Raises :class:`CredentialError` when neither yields
        a complete token pair.
        """
        if (
            settings.modal_token_id is not None
            and settings.modal_token_secret is not None
        ):
            return ModalCredentials(
                token_id=settings.modal_token_id,
                token_secret=settings.modal_token_secret,
                source="env",
                profile=None,
            )

        config_path = settings.modal_config_path.expanduser()
        profile = settings.modal_profile or "default"
        if not config_path.is_file():
            msg = (
                f"no Modal credentials available: env vars unset and "
                f"{config_path} does not exist"
            )
            raise CredentialError(msg)

        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            msg = f"could not parse Modal config file {config_path}: {exc}"
            raise CredentialError(msg) from exc

        section = data.get(profile)
        if section is None:
            msg = (
                f"Modal config file {config_path} has no profile '{profile}'; "
                f"available profiles: {sorted(data.keys())!r}"
            )
            raise CredentialError(msg)

        token_id = section.get("token_id")
        token_secret = section.get("token_secret")
        if not token_id or not token_secret:
            missing = [
                name
                for name, value in (
                    ("token_id", token_id),
                    ("token_secret", token_secret),
                )
                if not value
            ]
            msg = (
                f"Modal config file {config_path} profile '{profile}' is "
                f"missing required keys: {missing!r}"
            )
            raise CredentialError(msg)

        return ModalCredentials(
            token_id=SecretStr(str(token_id)),
            token_secret=SecretStr(str(token_secret)),
            source="toml",
            profile=profile,
            config_path=config_path,
        )


__all__ = [
    "CredentialError",
    "CredentialSource",
    "CredentialSourceKind",
    "ModalCredentials",
]
