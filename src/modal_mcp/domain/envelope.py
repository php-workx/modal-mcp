"""Canonical response envelopes for Modal MCP tools."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ErrorPayload, ModalAdapterError

T = TypeVar("T")


class PaginationMetadata(BaseModel):
    """Shared cursor pagination metadata."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    next_cursor: str | None = None


class ToolEnvelope(PaginationMetadata, Generic[T]):  # noqa: UP046
    """Canonical envelope for every Modal MCP tool output."""

    ok: bool
    request_id: str
    warnings: list[str] = Field(default_factory=list)
    data: T | None = None
    error: ErrorPayload | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> ToolEnvelope[T]:
        """Ensure success and error envelopes keep a coherent shape."""

        if self.ok:
            if self.data is None:
                raise ValueError("successful envelopes require a data payload")
            if self.error is not None:
                raise ValueError("successful envelopes cannot include error payloads")
        elif self.error is None:
            raise ValueError("error envelopes require an error payload")
        elif self.data is not None:
            raise ValueError("error envelopes cannot include a data payload")
        return self

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Dump envelopes while omitting unset optional fields."""

        kwargs.setdefault("exclude_none", True)
        return super().model_dump(*args, **kwargs)


def ok(  # noqa: UP047
    data: T,
    *,
    request_id: str,
    warnings: Sequence[str] | None = None,
    next_cursor: str | None = None,
) -> ToolEnvelope[T]:
    """Build a successful tool envelope."""

    return ToolEnvelope(
        ok=True,
        request_id=request_id,
        warnings=list(warnings or []),
        data=data,
        next_cursor=next_cursor,
    )


def error_result(
    error: ErrorPayload | ModalAdapterError,
    *,
    request_id: str,
    warnings: Sequence[str] | None = None,
    next_cursor: str | None = None,
) -> ToolEnvelope[Any]:
    """Build an error tool envelope from a structured adapter error."""

    payload = error if isinstance(error, ErrorPayload) else error.to_payload()
    return ToolEnvelope(
        ok=False,
        request_id=request_id,
        warnings=list(warnings or []),
        next_cursor=next_cursor,
        error=payload,
    )


__all__ = ["PaginationMetadata", "ToolEnvelope", "error_result", "ok"]
