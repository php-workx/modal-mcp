"""Contract tests for Modal fixture capture and replay metadata."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "capture_modal_fixtures",
    ROOT / "scripts/capture_modal_fixtures.py",
)
assert SPEC is not None and SPEC.loader is not None
capture_modal_fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_modal_fixtures)


def test_fixture_readme_documents_required_metadata() -> None:
    """Fixture docs name every required capture field."""

    text = (ROOT / "tests/fixtures/modal/README.md").read_text()

    for pattern in (
        "source_modal_version",
        "captured_at",
        "protobuf",
        "redacted",
    ):
        assert pattern in text


def test_sample_fixture_contains_required_metadata() -> None:
    """Committed replay fixtures carry deterministic metadata."""

    fixture = json.loads(
        (ROOT / "tests/fixtures/modal/app_list.sample.json").read_text()
    )

    assert fixture["source_modal_version"] == "1.4.1"
    assert fixture["captured_at"]
    assert fixture["rpc"] == "AppList"
    assert "request_metadata" in fixture
    assert "protobuf_wire_bytes_b64" in fixture
    assert "response_payload" in fixture


def test_capture_helper_redacts_secret_like_values() -> None:
    """Capture helper emits redacted deterministic payloads."""

    fixture = capture_modal_fixtures.build_fixture(
        rpc="AppList",
        source_modal_version="1.4.1",
        request_metadata={"authorization": "token-secret"},
        response_payload={"service_user": "as-secret-user"},
        protobuf_wire_bytes=b"abc",
    )

    assert fixture["request_metadata"]["authorization"] == "[REDACTED]"
    assert fixture["response_payload"]["service_user"] == "[REDACTED]"
    assert fixture["protobuf_wire_bytes_b64"] == "YWJj"
