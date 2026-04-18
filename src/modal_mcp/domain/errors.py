"""Domain error types and public error payloads."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    """Normalized public error codes exposed by the adapter."""

    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    PARSE_ERROR = "PARSE_ERROR"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    TIMEOUT = "TIMEOUT"
    INTERNAL_DRIFT = "INTERNAL_DRIFT"


def _coerce_error_code(code: ErrorCode | str) -> ErrorCode:
    """Normalize a caller-supplied code to a public error code enum."""

    if isinstance(code, ErrorCode):
        return code

    normalized = code.strip().upper().replace("-", "_")
    try:
        return ErrorCode(normalized)
    except ValueError as exc:  # pragma: no cover - defensive normalization guard
        raise ValueError(f"Unsupported error code: {code!r}") from exc


class ErrorPayload(BaseModel):
    """Structured, safe error payload exposed to MCP clients."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None
    debug: dict[str, Any] | None = None

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Dump payload data while omitting unset optional fields."""

        kwargs.setdefault("exclude_none", True)
        return super().model_dump(*args, **kwargs)

    @classmethod
    def from_error(cls, error: ModalAdapterError) -> ErrorPayload:
        """Build a payload from a structured adapter exception."""

        payload: dict[str, Any] = {
            "code": error.code,
            "message": error.safe_message,
        }
        if error.details is not None:
            payload["details"] = dict(error.details)
        if error.debug:
            payload["debug"] = dict(error.debug)
        return cls(**payload)


class ModalAdapterError(Exception):
    """Structured adapter exception with a public error code."""

    def __init__(
        self,
        code: ErrorCode | str,
        safe_message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
        debug: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = _coerce_error_code(code)
        self.safe_message = safe_message
        self.retryable = retryable
        self.details = dict(details) if details is not None else None
        self.debug = dict(debug) if debug is not None else {}
        super().__init__(safe_message)

    def to_payload(self) -> ErrorPayload:
        """Convert the exception into a public error payload."""

        return ErrorPayload.from_error(self)


__all__ = ["ErrorCode", "ErrorPayload", "ModalAdapterError"]
