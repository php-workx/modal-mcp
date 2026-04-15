"""Unit tests for domain error normalization."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from modal_mcp.domain.errors import ErrorCode, ErrorPayload, ModalAdapterError


def test_modal_adapter_error_normalizes_public_code() -> None:
    """String codes should normalize to the public enum values."""

    error = ModalAdapterError("not_found", "App was not found.")

    assert error.code is ErrorCode.NOT_FOUND
    assert error.safe_message == "App was not found."
    assert error.retryable is False
    assert str(error) == "App was not found."


def test_error_payload_omits_debug_when_not_provided() -> None:
    """Public error payloads should not expose absent debug data."""

    payload = ErrorPayload(
        code=ErrorCode.NOT_FOUND,
        message="App was not found.",
        details={"app_ref": "mref1.app.sig"},
    )

    assert payload.model_dump(mode="json") == {
        "code": "NOT_FOUND",
        "message": "App was not found.",
        "details": {"app_ref": "mref1.app.sig"},
    }
