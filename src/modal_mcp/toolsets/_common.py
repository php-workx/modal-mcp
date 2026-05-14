"""Shared helpers for FastMCP toolsets."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel

from modal_mcp.domain.envelope import ToolEnvelope, error_result, ok
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.domain.models import Page

READ_ONLY_ANNOTATIONS = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
MUTATING_ANNOTATIONS = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
REQUEST_ID = "tool-call"


def envelope[T: BaseModel](data: T) -> ToolEnvelope[T]:
    """Wrap a concrete model in the standard success envelope."""

    return ok(data, request_id=REQUEST_ID)


def page_envelope[T: BaseModel](items: Sequence[T]) -> ToolEnvelope[Page[T]]:
    """Wrap a sequence in the standard paged success envelope."""

    return ok(
        Page[T](items=list(items), truncated=False),
        request_id=REQUEST_ID,
    )


def page_envelope_partial[T: BaseModel](
    items: Sequence[T],
    warnings: Sequence[str],
) -> ToolEnvelope[Page[T]]:
    """Wrap a partial sequence with normalization warnings in a success envelope."""

    return ok(
        Page[T](items=list(items), truncated=False),
        request_id=REQUEST_ID,
        warnings=list(warnings),
    )


def not_found(message: str) -> ToolEnvelope[Any]:
    """Return a normalized not-found tool error."""

    return error_result(
        ModalAdapterError(ErrorCode.NOT_FOUND, message),
        request_id=REQUEST_ID,
    )


def disabled_error(tool_name: str, details: dict[str, Any]) -> ToolEnvelope[Any]:
    """Return a normalized disabled-capability error."""

    return error_result(
        ModalAdapterError(
            ErrorCode.POLICY_BLOCKED,
            f"{tool_name} is disabled in Modal MCP v1",
            details=details,
        ),
        request_id=REQUEST_ID,
    )


__all__ = [
    "MUTATING_ANNOTATIONS",
    "READ_ONLY_ANNOTATIONS",
    "REQUEST_ID",
    "disabled_error",
    "envelope",
    "not_found",
    "page_envelope",
    "page_envelope_partial",
]
