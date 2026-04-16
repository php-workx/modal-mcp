"""Unit tests for Modal MCP settings."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from modal_mcp.config import (
    SECRET_ENV_KEYS,
    ConfigError,
    Settings,
    _supports_expert_proc_masking,
    load_secret_file,
    scrub_secret_env,
)

CONFIG_ENV_KEYS = {
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "MODAL_TOKEN_ID_FILE",
    "MODAL_TOKEN_SECRET_FILE",
    "MODAL_CONFIG_PATH",
    "MODAL_ENVIRONMENT",
    "MODAL_MCP_HTTP_BIND",
    "MODAL_MCP_PUBLIC_ORIGIN",
    "MODAL_MCP_ALLOWED_ORIGINS",
    "MODAL_MCP_ALLOWED_HOSTS",
    "MODAL_MCP_AUTH_MODE",
    "MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE",
    "MODAL_MCP_PUBLIC_BASE_URL",
    "MODAL_MCP_ALLOWED_CLIENT_REDIRECT_URIS",
    "MODAL_MCP_HOSTED_AUTH_ISSUER",
    "MODAL_MCP_HOSTED_JWKS_URI",
    "MODAL_MCP_HOSTED_AUDIENCE",
    "MODAL_MCP_AUTH_ISSUER",
    "MODAL_MCP_AUTH_JWKS_URI",
    "MODAL_MCP_AUTH_AUDIENCE",
    "MODAL_MCP_ALLOWED_REDIRECT_URIS",
    "MODAL_MCP_READ_ONLY",
    "MODAL_MCP_ENABLED_TOOLSETS",
    "MODAL_MCP_SIGNING_KEYS",
    "MODAL_MCP_SIGNING_KEY_FILE",
    "MODAL_MCP_AUDIT_LOG",
    "MODAL_MCP_AUDIT_READ_SAMPLE",
    "MODAL_MCP_RATE_LIMIT_RPS",
    "MODAL_MCP_MUTATION_RATE_LIMIT_SECONDS",
    "MODAL_MCP_MAX_LIST_ITEMS",
    "MODAL_MCP_LOG_LEVEL",
    "MODAL_MCP_OTEL_EXPORTER",
    "MODAL_MCP_DEBUG_EXPOSE_IDS",
    "MODAL_MCP_ALLOW_CROSS_ENV",
    "MODAL_MCP_DEBUG",
    "MODAL_MCP_APPROVAL_LEDGER",
    "MODAL_MCP_CLI_FALLBACK",
}


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep settings tests independent from the operator environment."""

    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def modal_config_path(tmp_path: Path) -> Path:
    """Create a placeholder Modal config file for credential fallback tests."""

    path = tmp_path / "modal.toml"
    path.write_text("[default]\n", encoding="utf-8")
    return path


def base_settings_kwargs(modal_config_path: Path) -> dict[str, object]:
    """Return the minimum self-hosted settings required at startup."""

    return {
        "modal_config_path": modal_config_path,
        "modal_mcp_allowed_origins": ("http://127.0.0.1:8765",),
        "modal_mcp_signing_keys": SecretStr("kid1:" + "a" * 64),
    }


def test_settings_parse_env_defaults_and_comma_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings read every startup-critical env var and parse comma lists."""

    monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "tsecret")
    monkeypatch.setenv("MODAL_MCP_ALLOWED_ORIGINS", "http://a.test, http://b.test")
    monkeypatch.setenv("MODAL_MCP_ALLOWED_HOSTS", "a.test,b.test")
    monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", "kid1:" + "a" * 64)
    monkeypatch.setenv("MODAL_MCP_AUDIT_READ_SAMPLE", "0.25")
    monkeypatch.setenv("MODAL_MCP_MUTATION_RATE_LIMIT_SECONDS", "45")
    monkeypatch.setenv("MODAL_MCP_MAX_LIST_ITEMS", "500")
    monkeypatch.setenv("MODAL_MCP_OTEL_EXPORTER", "http://otel.test:4318")

    settings = Settings()

    assert settings.modal_mcp_http_bind == "127.0.0.1:8765"
    assert settings.modal_mcp_allowed_origins == (
        "http://a.test",
        "http://b.test",
    )
    assert settings.modal_mcp_allowed_hosts == ("a.test", "b.test")
    assert settings.modal_mcp_auth_mode == "self_hosted_byo_token"
    assert settings.modal_mcp_read_only is True
    assert settings.modal_mcp_enabled_toolsets == (
        "discovery",
        "apps",
        "containers",
        "logs",
        "volumes",
        "sandboxes",
    )
    assert settings.modal_mcp_audit_log == "stdout"
    assert settings.modal_mcp_audit_read_sample == 0.25
    assert settings.modal_mcp_rate_limit_rps == 5.0
    assert settings.modal_mcp_mutation_rate_limit_seconds == 45
    assert settings.modal_mcp_max_list_items == 500
    assert settings.modal_mcp_log_level == "info"
    assert settings.modal_mcp_otel_exporter == "http://otel.test:4318"
    assert settings.modal_mcp_debug_expose_ids is False
    assert settings.modal_mcp_allow_cross_env is False
    assert settings.modal_mcp_debug is False
    assert settings.modal_mcp_cli_fallback is False


def test_file_backed_secrets_load_into_secret_str(tmp_path: Path) -> None:
    """Secret files trim a single trailing newline and reject empty files."""

    token_id_file = tmp_path / "token-id"
    token_secret_file = tmp_path / "token-secret"
    signing_key_file = tmp_path / "signing-key"
    empty_file = tmp_path / "empty"
    token_id_file.write_text("tid\n", encoding="utf-8")
    token_secret_file.write_text("tsecret\n", encoding="utf-8")
    signing_key_file.write_text("kid1:" + "a" * 64 + "\n", encoding="utf-8")
    empty_file.write_text("", encoding="utf-8")

    settings = Settings(
        modal_token_id_file=token_id_file,
        modal_token_secret_file=token_secret_file,
        modal_mcp_signing_key_file=signing_key_file,
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
    )

    assert settings.modal_token_id is not None
    assert settings.modal_token_id.get_secret_value() == "tid"
    assert settings.modal_token_secret is not None
    assert settings.modal_token_secret.get_secret_value() == "tsecret"
    assert settings.modal_mcp_signing_keys is not None
    assert settings.modal_mcp_signing_keys.get_secret_value() == "kid1:" + "a" * 64
    with pytest.raises(ConfigError, match="empty"):
        load_secret_file(empty_file)


def test_required_startup_material_fails_fast(modal_config_path: Path) -> None:
    """Missing required origins, signing keys, and token pairs are rejected."""

    with pytest.raises(ValidationError, match="MODAL_MCP_ALLOWED_ORIGINS"):
        Settings(
            modal_config_path=modal_config_path,
            modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
        )

    with pytest.raises(ValidationError, match="MODAL_MCP_SIGNING_KEYS"):
        Settings(
            modal_config_path=modal_config_path,
            modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        )

    with pytest.raises(ValidationError, match="provided together"):
        Settings(
            modal_token_id=SecretStr("tid"),
            modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
            modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
        )


def test_hosted_mode_requires_idp_and_refuses_debug_flags(
    modal_config_path: Path,
) -> None:
    """Hosted auth requires verifier settings and forbids debug/fallback flags."""

    kwargs = base_settings_kwargs(modal_config_path)
    with pytest.raises(ValidationError, match="MODAL_MCP_AUTH_ISSUER"):
        Settings(modal_mcp_auth_mode="hosted_jwt", **kwargs)

    with pytest.raises(ValidationError, match="MODAL_MCP_DEBUG"):
        Settings(
            modal_mcp_auth_mode="hosted_jwt",
            modal_mcp_public_origin="https://mcp.example.com",
            modal_mcp_auth_issuer="https://issuer.example.com",
            modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
            modal_mcp_auth_audience="modal-mcp",
            modal_mcp_allowed_redirect_uris=("https://client.example.com/cb",),
            modal_mcp_debug=True,
            **kwargs,
        )

    with pytest.raises(ValidationError, match="MODAL_MCP_ALLOWED_REDIRECT_URIS"):
        Settings(
            modal_mcp_auth_mode="hosted_oauth",
            modal_mcp_public_origin="https://mcp.example.com",
            modal_mcp_auth_issuer="https://issuer.example.com",
            modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
            modal_mcp_auth_audience="modal-mcp",
            **kwargs,
        )

    hosted = Settings(
        modal_mcp_auth_mode="hosted_jwt",
        modal_mcp_public_origin="https://mcp.example.com",
        modal_mcp_auth_issuer="https://issuer.example.com",
        modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
        modal_mcp_auth_audience="modal-mcp",
        modal_mcp_allowed_redirect_uris=("https://client.example.com/cb",),
        **kwargs,
    )
    assert hosted.modal_mcp_auth_mode == "hosted_read_only_ephemeral"


def test_hosted_mode_reports_canonical_missing_env_names(
    modal_config_path: Path,
) -> None:
    """Hosted-mode config errors should name the canonical env vars."""

    kwargs = base_settings_kwargs(modal_config_path)
    with pytest.raises(ValidationError, match="MODAL_MCP_AUTH_ISSUER"):
        Settings(modal_mcp_auth_mode="hosted_jwt", **kwargs)

    with pytest.raises(ValidationError, match="MODAL_MCP_ALLOWED_REDIRECT_URIS"):
        Settings(
            modal_mcp_auth_mode="hosted_jwt",
            modal_mcp_public_origin="https://mcp.example.com",
            modal_mcp_auth_issuer="https://issuer.example.com",
            modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
            modal_mcp_auth_audience="modal-mcp",
            **kwargs,
        )


def test_hosted_mode_aliases_normalize_to_hosted_read_only_ephemeral(
    modal_config_path: Path,
) -> None:
    """Legacy hosted modes normalize to the current canonical mode string."""

    hosted_oauth = Settings(
        modal_mcp_auth_mode="hosted_oauth",
        modal_mcp_public_origin="https://mcp.example.com",
        modal_mcp_auth_issuer="https://issuer.example.com",
        modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
        modal_mcp_auth_audience="modal-mcp",
        modal_mcp_allowed_redirect_uris=("https://client.example.com/cb",),
        **base_settings_kwargs(modal_config_path),
    )

    hosted_jwt = Settings(
        modal_mcp_auth_mode="hosted_jwt",
        **base_settings_kwargs(modal_config_path),
        **{
            "MODAL_MCP_PUBLIC_BASE_URL": "https://mcp.example.com",
            "MODAL_MCP_HOSTED_AUTH_ISSUER": "https://issuer.example.com",
            "MODAL_MCP_HOSTED_JWKS_URI": "https://issuer.example.com/jwks.json",
            "MODAL_MCP_HOSTED_AUDIENCE": "modal-mcp",
            "MODAL_MCP_ALLOWED_CLIENT_REDIRECT_URIS": (
                "https://client.example.com/cb",
            ),
        },
    )

    assert hosted_oauth.modal_mcp_auth_mode == "hosted_read_only_ephemeral"
    assert hosted_jwt.modal_mcp_auth_mode == "hosted_read_only_ephemeral"
    assert hosted_jwt.modal_mcp_public_origin == "https://mcp.example.com"
    assert hosted_jwt.modal_mcp_auth_issuer == "https://issuer.example.com"
    assert hosted_jwt.modal_mcp_auth_jwks_uri == "https://issuer.example.com/jwks.json"
    assert hosted_jwt.modal_mcp_auth_audience == "modal-mcp"
    assert hosted_jwt.modal_mcp_allowed_redirect_uris == (
        "https://client.example.com/cb",
    )


def test_expert_proc_masking_helper_uses_real_proc_layout(
    tmp_path: Path,
) -> None:
    """The proc masking helper should work against a real filesystem layout."""

    proc_root = tmp_path / "proc"
    self_root = proc_root / "self"
    self_root.mkdir(parents=True)
    (self_root / "environ").write_text("", encoding="utf-8")
    (self_root / "maps").write_text("", encoding="utf-8")
    (self_root / "cmdline").write_text("", encoding="utf-8")
    (proc_root / str(os.getpid())).mkdir(parents=True)
    (proc_root / str(os.getpid()) / "cmdline").write_text("", encoding="utf-8")

    assert _supports_expert_proc_masking(
        proc_root=proc_root,
        mount_command_lookup=lambda _: "/usr/bin/mount",
    )


def test_expert_proc_masking_helper_refuses_missing_mount_binary(
    tmp_path: Path,
) -> None:
    """Missing proc masking prerequisites should be rejected deterministically."""

    proc_root = tmp_path / "proc"
    self_root = proc_root / "self"
    self_root.mkdir(parents=True)
    (self_root / "environ").write_text("", encoding="utf-8")
    (self_root / "maps").write_text("", encoding="utf-8")
    (self_root / "cmdline").write_text("", encoding="utf-8")
    (proc_root / str(os.getpid())).mkdir(parents=True)
    (proc_root / str(os.getpid()) / "cmdline").write_text("", encoding="utf-8")

    assert not _supports_expert_proc_masking(
        proc_root=proc_root,
        mount_command_lookup=lambda _: None,
    )


def test_scrub_secret_env_removes_secret_carriers(
    monkeypatch: pytest.MonkeyPatch,
    modal_config_path: Path,
) -> None:
    """Environment scrubbing removes secret-bearing keys after Settings loads."""

    for key in SECRET_ENV_KEYS:
        monkeypatch.setenv(key, "secret")
    settings = Settings(**base_settings_kwargs(modal_config_path))

    removed = scrub_secret_env(settings)

    assert removed >= SECRET_ENV_KEYS
    assert all(key not in os.environ for key in SECRET_ENV_KEYS)
