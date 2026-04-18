"""Disabled-by-default CLI fallback helpers.

This module is intentionally dead code unless ``MODAL_MCP_CLI_FALLBACK=true``
is set. It keeps subprocess usage constrained so any future opt-in remains
explicit, predictable, and easy to audit.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

CLI_FALLBACK_ENV_VAR = "MODAL_MCP_CLI_FALLBACK"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_OUTPUT_CHARS = 8_192
WHITELISTED_ENV_KEYS: tuple[str, ...] = ("PATH", "HOME", "LANG")
ALLOWED_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("modal", "apps", "list"),
    ("modal", "apps", "get"),
    ("modal", "containers", "list"),
    ("modal", "discovery", "list"),
    ("modal", "logs", "list"),
    ("modal", "sandboxes", "list"),
    ("modal", "volumes", "list"),
)


class CliFallbackDisabledError(RuntimeError):
    """Raised when the dead CLI fallback is used without explicit opt-in."""


class CliFallbackCommandError(ValueError):
    """Raised when a CLI command falls outside the strict allowlist."""


@dataclass(frozen=True, slots=True)
class CliFallbackContext:
    """Minimal credential context needed by the fallback CLI wrapper."""

    modal_token_id: str
    modal_token_secret: str
    modal_config_path: str
    modal_environment: str | None = None

    def as_env(self) -> dict[str, str]:
        """Build the minimal environment whitelist for subprocess execution."""

        env = {
            key: value
            for key in WHITELISTED_ENV_KEYS
            if (value := os.environ.get(key)) is not None
        }
        env["MODAL_TOKEN_ID"] = self.modal_token_id
        env["MODAL_TOKEN_SECRET"] = self.modal_token_secret
        env["MODAL_CONFIG_PATH"] = self.modal_config_path
        if self.modal_environment is not None:
            env["MODAL_ENVIRONMENT"] = self.modal_environment
        return env


def is_cli_fallback_enabled() -> bool:
    """Return whether the explicitly disabled fallback has been opted into."""

    return os.environ.get(CLI_FALLBACK_ENV_VAR, "").strip().lower() == "true"


def require_cli_fallback_enabled() -> None:
    """Reject any attempt to use the fallback unless it was explicitly enabled."""

    if not is_cli_fallback_enabled():
        msg = (
            "CLI fallback is disabled by default; set "
            f"{CLI_FALLBACK_ENV_VAR}=true to opt in"
        )
        raise CliFallbackDisabledError(msg)


def _validate_command(command: Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)):
        msg = "command must be a sequence of arguments, not a string"
        raise TypeError(msg)

    command_tuple = tuple(command)
    if not command_tuple:
        msg = "command must not be empty"
        raise ValueError(msg)
    if any(not isinstance(part, str) or not part for part in command_tuple):
        msg = "command arguments must be non-empty strings"
        raise TypeError(msg)
    if not any(
        command_tuple[: len(prefix)] == prefix for prefix in ALLOWED_COMMAND_PREFIXES
    ):
        joined = " ".join(shlex.quote(part) for part in command_tuple)
        msg = f"command is not allowlisted: {joined}"
        raise CliFallbackCommandError(msg)
    return command_tuple


def _redact_text(text: str, redactions: Sequence[str]) -> str:
    redacted = text
    for secret in redactions:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _cap_text(text: str, max_chars: int) -> str:
    if max_chars < 0:
        msg = "max_output_chars must be non-negative"
        raise ValueError(msg)
    return text if len(text) <= max_chars else text[:max_chars]


def run_modal_cli(
    command: Sequence[str],
    *,
    context: CliFallbackContext,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    redact_values: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    """Run the Modal CLI in a tightly constrained compatibility mode."""

    require_cli_fallback_enabled()
    command_tuple = _validate_command(command)

    completed = subprocess.run(
        list(command_tuple),
        shell=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=context.as_env(),
    )

    stdout = _cap_text(
        _redact_text(completed.stdout or "", redact_values),
        max_output_chars,
    )
    stderr = _cap_text(
        _redact_text(completed.stderr or "", redact_values),
        max_output_chars,
    )
    return subprocess.CompletedProcess(
        args=list(command_tuple),
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
    )


__all__ = [
    "ALLOWED_COMMAND_PREFIXES",
    "CLI_FALLBACK_ENV_VAR",
    "DEFAULT_MAX_OUTPUT_CHARS",
    "DEFAULT_TIMEOUT_SECONDS",
    "CliFallbackCommandError",
    "CliFallbackContext",
    "CliFallbackDisabledError",
    "is_cli_fallback_enabled",
    "require_cli_fallback_enabled",
    "run_modal_cli",
]
