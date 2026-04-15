"""Configuration loading and startup safety checks for Modal MCP."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AuthMode = Literal["self_hosted_byo_token", "hosted_jwt", "hosted_oauth"]
LogLevel = Literal["trace", "debug", "info", "warn", "error"]

HOSTED_AUTH_MODES: frozenset[str] = frozenset({"hosted_jwt", "hosted_oauth"})
DEFAULT_TOOLSETS: tuple[str, ...] = (
    "discovery",
    "apps",
    "containers",
    "logs",
    "volumes",
    "sandboxes",
)
SECRET_ENV_KEYS: frozenset[str] = frozenset(
    {
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "MODAL_TOKEN_ID_FILE",
        "MODAL_TOKEN_SECRET_FILE",
        "MODAL_MCP_SIGNING_KEYS",
        "MODAL_MCP_SIGNING_KEY_FILE",
        "MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE",
    }
)


class ConfigError(ValueError):
    """Raised when startup configuration violates the Modal MCP contract."""


def _comma_separated(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, Iterable):
        return tuple(str(part).strip() for part in value if str(part).strip())
    msg = "expected a comma-separated string or iterable"
    raise TypeError(msg)


def load_secret_file(path: str | Path) -> SecretStr:
    """Read a file-backed secret and reject missing or empty material."""

    secret_path = Path(path).expanduser()
    try:
        value = secret_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        msg = f"secret file does not exist: {secret_path}"
        raise ConfigError(msg) from exc
    except OSError as exc:
        msg = f"unable to read secret file: {secret_path}"
        raise ConfigError(msg) from exc

    if value.endswith("\n"):
        value = value[:-1]
    if value.endswith("\r"):
        value = value[:-1]
    if not value:
        msg = f"secret file is empty: {secret_path}"
        raise ConfigError(msg)
    return SecretStr(value)


class Settings(BaseSettings):
    """Pydantic settings for the self-hosted Modal MCP server."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=True,
        populate_by_name=True,
        extra="ignore",
    )

    modal_token_id: SecretStr | None = Field(
        default=None,
        validation_alias="MODAL_TOKEN_ID",
    )
    modal_token_secret: SecretStr | None = Field(
        default=None,
        validation_alias="MODAL_TOKEN_SECRET",
    )
    modal_token_id_file: Path | None = Field(
        default=None,
        validation_alias="MODAL_TOKEN_ID_FILE",
    )
    modal_token_secret_file: Path | None = Field(
        default=None,
        validation_alias="MODAL_TOKEN_SECRET_FILE",
    )
    modal_config_path: Path = Field(
        default=Path("~/.modal.toml"),
        validation_alias="MODAL_CONFIG_PATH",
    )
    modal_environment: str | None = Field(
        default=None,
        validation_alias="MODAL_ENVIRONMENT",
    )

    modal_mcp_http_bind: str = Field(
        default="127.0.0.1:8765",
        validation_alias="MODAL_MCP_HTTP_BIND",
    )
    modal_mcp_public_origin: str | None = Field(
        default=None,
        validation_alias="MODAL_MCP_PUBLIC_ORIGIN",
    )
    modal_mcp_allowed_origins: Annotated[tuple[str, ...], NoDecode] = Field(
        validation_alias="MODAL_MCP_ALLOWED_ORIGINS",
    )
    modal_mcp_allowed_hosts: Annotated[tuple[str, ...], NoDecode] = Field(
        default=("127.0.0.1", "localhost"),
        validation_alias="MODAL_MCP_ALLOWED_HOSTS",
    )

    modal_mcp_auth_mode: AuthMode = Field(
        default="self_hosted_byo_token",
        validation_alias="MODAL_MCP_AUTH_MODE",
    )
    modal_mcp_self_hosted_bearer_token_file: Path | None = Field(
        default=None,
        validation_alias="MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE",
    )
    modal_mcp_auth_issuer: str | None = Field(
        default=None,
        validation_alias="MODAL_MCP_AUTH_ISSUER",
    )
    modal_mcp_auth_jwks_uri: str | None = Field(
        default=None,
        validation_alias="MODAL_MCP_AUTH_JWKS_URI",
    )
    modal_mcp_auth_audience: str | None = Field(
        default=None,
        validation_alias="MODAL_MCP_AUTH_AUDIENCE",
    )
    modal_mcp_allowed_redirect_uris: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        validation_alias="MODAL_MCP_ALLOWED_REDIRECT_URIS",
    )

    modal_mcp_read_only: bool = Field(
        default=True,
        validation_alias="MODAL_MCP_READ_ONLY",
    )
    modal_mcp_enabled_toolsets: Annotated[tuple[str, ...], NoDecode] = Field(
        default=DEFAULT_TOOLSETS,
        validation_alias="MODAL_MCP_ENABLED_TOOLSETS",
    )
    modal_mcp_signing_keys: SecretStr | None = Field(
        default=None,
        validation_alias="MODAL_MCP_SIGNING_KEYS",
    )
    modal_mcp_signing_key_file: Path | None = Field(
        default=None,
        validation_alias="MODAL_MCP_SIGNING_KEY_FILE",
    )

    modal_mcp_audit_log: str = Field(
        default="stdout",
        validation_alias="MODAL_MCP_AUDIT_LOG",
    )
    modal_mcp_audit_read_sample: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        validation_alias="MODAL_MCP_AUDIT_READ_SAMPLE",
    )
    modal_mcp_rate_limit_rps: float = Field(
        default=5.0,
        gt=0.0,
        validation_alias="MODAL_MCP_RATE_LIMIT_RPS",
    )
    modal_mcp_mutation_rate_limit_seconds: int = Field(
        default=30,
        ge=0,
        validation_alias="MODAL_MCP_MUTATION_RATE_LIMIT_SECONDS",
    )
    modal_mcp_max_list_items: int = Field(
        default=10_000,
        ge=1,
        validation_alias="MODAL_MCP_MAX_LIST_ITEMS",
    )
    modal_mcp_log_level: LogLevel = Field(
        default="info",
        validation_alias="MODAL_MCP_LOG_LEVEL",
    )
    modal_mcp_otel_exporter: str | None = Field(
        default=None,
        validation_alias="MODAL_MCP_OTEL_EXPORTER",
    )

    modal_mcp_debug_expose_ids: bool = Field(
        default=False,
        validation_alias="MODAL_MCP_DEBUG_EXPOSE_IDS",
    )
    modal_mcp_allow_cross_env: bool = Field(
        default=False,
        validation_alias="MODAL_MCP_ALLOW_CROSS_ENV",
    )
    modal_mcp_debug: bool = Field(
        default=False,
        validation_alias="MODAL_MCP_DEBUG",
    )
    modal_mcp_approval_ledger: str | None = Field(
        default=None,
        validation_alias="MODAL_MCP_APPROVAL_LEDGER",
    )
    modal_mcp_cli_fallback: bool = Field(
        default=False,
        validation_alias="MODAL_MCP_CLI_FALLBACK",
    )

    @field_validator(
        "modal_mcp_allowed_origins",
        "modal_mcp_allowed_hosts",
        "modal_mcp_allowed_redirect_uris",
        "modal_mcp_enabled_toolsets",
        mode="before",
    )
    @classmethod
    def _parse_csv(cls, value: Any) -> tuple[str, ...]:
        return _comma_separated(value)

    @model_validator(mode="after")
    def _load_file_backed_secrets(self) -> Settings:
        if self.modal_token_id is None and self.modal_token_id_file is not None:
            self.modal_token_id = load_secret_file(self.modal_token_id_file)
        if self.modal_token_secret is None and self.modal_token_secret_file is not None:
            self.modal_token_secret = load_secret_file(self.modal_token_secret_file)
        if (
            self.modal_mcp_signing_keys is None
            and self.modal_mcp_signing_key_file is not None
        ):
            self.modal_mcp_signing_keys = load_secret_file(
                self.modal_mcp_signing_key_file
            )
        return self

    @model_validator(mode="after")
    def _validate_startup_contract(self) -> Settings:
        if not self.modal_mcp_allowed_origins:
            msg = "MODAL_MCP_ALLOWED_ORIGINS must be non-empty"
            raise ConfigError(msg)
        if not self.modal_mcp_allowed_hosts:
            msg = "MODAL_MCP_ALLOWED_HOSTS must be non-empty"
            raise ConfigError(msg)
        if not self.modal_mcp_enabled_toolsets:
            msg = "MODAL_MCP_ENABLED_TOOLSETS must be non-empty"
            raise ConfigError(msg)
        if self.modal_mcp_signing_keys is None:
            msg = "MODAL_MCP_SIGNING_KEYS or MODAL_MCP_SIGNING_KEY_FILE is required"
            raise ConfigError(msg)
        if bool(self.modal_token_id) != bool(self.modal_token_secret):
            msg = "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be provided together"
            raise ConfigError(msg)
        if not self._has_modal_credentials():
            msg = (
                "Modal credentials are required via MODAL_TOKEN_ID/SECRET, "
                "MODAL_TOKEN_*_FILE, or MODAL_CONFIG_PATH"
            )
            raise ConfigError(msg)

        validate_hosted_debug_flags(self)
        if self.modal_mcp_auth_mode in HOSTED_AUTH_MODES:
            self._validate_hosted_auth()
        return self

    def _has_modal_credentials(self) -> bool:
        if self.modal_token_id is not None and self.modal_token_secret is not None:
            return True
        return self.modal_config_path.expanduser().is_file()

    def _validate_hosted_auth(self) -> None:
        missing = [
            name
            for name, value in (
                ("MODAL_MCP_PUBLIC_ORIGIN", self.modal_mcp_public_origin),
                ("MODAL_MCP_AUTH_ISSUER", self.modal_mcp_auth_issuer),
                ("MODAL_MCP_AUTH_JWKS_URI", self.modal_mcp_auth_jwks_uri),
                ("MODAL_MCP_AUTH_AUDIENCE", self.modal_mcp_auth_audience),
            )
            if not value
        ]
        if not self.modal_mcp_allowed_redirect_uris:
            missing.append("MODAL_MCP_ALLOWED_REDIRECT_URIS")
        if missing:
            msg = f"hosted auth mode is missing required settings: {', '.join(missing)}"
            raise ConfigError(msg)


def validate_hosted_debug_flags(settings: Settings) -> None:
    """Reject debug-only escape hatches in hosted credential modes."""

    if settings.modal_mcp_auth_mode not in HOSTED_AUTH_MODES:
        return
    forbidden: list[str] = []
    if settings.modal_mcp_debug:
        forbidden.append("MODAL_MCP_DEBUG")
    if settings.modal_mcp_debug_expose_ids:
        forbidden.append("MODAL_MCP_DEBUG_EXPOSE_IDS")
    if settings.modal_mcp_cli_fallback:
        forbidden.append("MODAL_MCP_CLI_FALLBACK")
    if forbidden:
        msg = "hosted auth mode refuses unsafe debug/fallback settings: " + ", ".join(
            forbidden
        )
        raise ConfigError(msg)


def scrub_secret_env(settings: Settings) -> frozenset[str]:
    """Remove env vars that carry Modal credentials or signing material."""

    del settings
    removed: set[str] = set()
    for key in SECRET_ENV_KEYS:
        if key in os.environ:
            removed.add(key)
            del os.environ[key]
    return frozenset(removed)


def assert_runtime_security(settings: Settings) -> None:
    """Apply best-effort process hardening checks for startup."""

    validate_hosted_debug_flags(settings)
    if os.name == "posix" and hasattr(ctypes, "CDLL"):
        try:
            libc = ctypes.CDLL(None)
            prctl = getattr(libc, "prctl", None)
            if prctl is not None:
                pr_set_dumpable = 4
                prctl(pr_set_dumpable, 0, 0, 0, 0)
        except (AttributeError, OSError, TypeError):
            return


__all__ = [
    "HOSTED_AUTH_MODES",
    "SECRET_ENV_KEYS",
    "AuthMode",
    "ConfigError",
    "LogLevel",
    "Settings",
    "assert_runtime_security",
    "load_secret_file",
    "scrub_secret_env",
    "validate_hosted_debug_flags",
]
