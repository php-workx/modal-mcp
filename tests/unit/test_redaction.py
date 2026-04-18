"""Unit tests for shared redaction and logger configuration."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
import structlog
from pydantic import SecretStr

from modal_mcp.config import Settings
from modal_mcp.observability.logger import configure_logging
from modal_mcp.observability.redact import (
    BASE64_REDACTION_PLACEHOLDER,
    REDACTION_PLACEHOLDER,
    collect_known_secrets,
    redact_value,
    structlog_redact_processor,
)


@pytest.fixture
def redaction_settings(tmp_path: Path) -> Settings:
    """Return settings with secrets available for exact redaction."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    return Settings(
        modal_config_path=modal_config,
        modal_token_id=SecretStr("token-id-secret"),
        modal_token_secret=SecretStr("token-secret-value"),
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "b" * 64),
    )


def test_redact_value_recurses_known_secrets_and_secret_shapes(
    redaction_settings: Settings,
) -> None:
    """Nested dict/list/tuple strings are scrubbed consistently."""

    known = collect_known_secrets(redaction_settings)
    aws_shaped_fixture = "AKIA" + "1234567890ABCDEF"
    value = {
        "exact": "prefix token-secret-value suffix",
        "nested": [
            "MODAL_TOKEN_SECRET=plain-text",
            ("as-service-user-123456", aws_shaped_fixture),
        ],
        "jwt": "eyJabc.def.ghi",
    }

    assert redact_value(value, known_secrets=known) == {
        "exact": f"prefix {REDACTION_PLACEHOLDER} suffix",
        "nested": [
            REDACTION_PLACEHOLDER,
            (REDACTION_PLACEHOLDER, REDACTION_PLACEHOLDER),
        ],
        "jwt": REDACTION_PLACEHOLDER,
    }


def test_redact_value_replaces_base64_payloads_that_decode_to_secrets() -> None:
    """Base64-looking payloads are decoded and rescanned defensively."""

    encoded = base64.b64encode(b"MODAL_TOKEN_SECRET=plain-text").decode("ascii")

    assert redact_value({"payload": encoded}) == {
        "payload": BASE64_REDACTION_PLACEHOLDER
    }


def test_structlog_configuration_orders_redactor_after_exception_formatting() -> None:
    """The redactor sits after format_exc_info and before JSONRenderer."""

    previous_config = structlog.get_config()
    try:
        configure_logging(known_secrets=("secret-value",))
        processors = list(structlog.get_config()["processors"])

        format_index = processors.index(structlog.processors.format_exc_info)
        redact_index = processors.index(structlog_redact_processor)
        json_index = next(
            index
            for index, processor in enumerate(processors)
            if isinstance(processor, structlog.processors.JSONRenderer)
        )

        assert format_index < redact_index < json_index
    finally:
        structlog.configure(**previous_config)
