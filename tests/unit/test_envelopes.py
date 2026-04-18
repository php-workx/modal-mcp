"""Unit tests for response envelopes."""

from __future__ import annotations

from pydantic import BaseModel

from modal_mcp.domain.envelope import ToolEnvelope, error_result, ok
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError


class DemoPayload(BaseModel):
    """Simple payload model for envelope tests."""

    count: int


def test_ok_wraps_data_and_pagination_metadata() -> None:
    """The success helper should preserve request and pagination metadata."""

    payload = DemoPayload(count=3)

    envelope = ok(
        payload,
        request_id="req-123",
        warnings=["partial result"],
        next_cursor="cursor-456",
    )

    assert envelope == ToolEnvelope[DemoPayload](
        ok=True,
        request_id="req-123",
        warnings=["partial result"],
        data=payload,
        next_cursor="cursor-456",
    )
    assert envelope.model_dump(mode="json") == {
        "ok": True,
        "request_id": "req-123",
        "warnings": ["partial result"],
        "data": {"count": 3},
        "next_cursor": "cursor-456",
    }


def test_error_result_wraps_normalized_error_payload() -> None:
    """The error helper should surface a public code and safe details."""

    error = ModalAdapterError(
        "timeout",
        "The request timed out.",
        retryable=True,
        details={"tool": "list_apps"},
        debug={"upstream": "deadline exceeded"},
    )

    envelope = error_result(
        error,
        request_id="req-789",
        warnings=["retry suggested"],
    )

    assert envelope.ok is False
    assert envelope.request_id == "req-789"
    assert envelope.warnings == ["retry suggested"]
    assert envelope.error is not None
    assert envelope.error.code is ErrorCode.TIMEOUT
    assert envelope.error.message == "The request timed out."
    assert envelope.error.details == {"tool": "list_apps"}
    assert envelope.error.debug == {"upstream": "deadline exceeded"}
    assert envelope.model_dump(mode="json") == {
        "ok": False,
        "request_id": "req-789",
        "warnings": ["retry suggested"],
        "error": {
            "code": "TIMEOUT",
            "message": "The request timed out.",
            "details": {"tool": "list_apps"},
            "debug": {"upstream": "deadline exceeded"},
        },
    }
