"""Unit tests for CredentialSource and ModalCredentials."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from modal_mcp.adapters.credentials import (
    CredentialError,
    CredentialSource,
    ModalCredentials,
)
from modal_mcp.config import Settings

SIGNING_KEY_TEXT = "kid1:" + "a" * 64


def _settings(
    tmp_path: Path,
    *,
    token_id: str | None = None,
    token_secret: str | None = None,
    modal_config_text: str | None = "[default]\n",
    profile: str | None = None,
) -> Settings:
    """Build minimal Settings with optional tokens / modal.toml / profile."""
    config_path = tmp_path / "modal.toml"
    if modal_config_text is not None:
        config_path.write_text(modal_config_text, encoding="utf-8")
    kwargs: dict[str, object] = {
        "modal_config_path": config_path,
        "modal_mcp_allowed_origins": ("http://127.0.0.1:8765",),
        "modal_mcp_signing_keys": SecretStr(SIGNING_KEY_TEXT),
    }
    if token_id is not None:
        kwargs["modal_token_id"] = SecretStr(token_id)
    if token_secret is not None:
        kwargs["modal_token_secret"] = SecretStr(token_secret)
    if profile is not None:
        kwargs["modal_profile"] = profile
    return Settings(**kwargs)


def _creds(**overrides: object) -> ModalCredentials:
    base: dict[str, object] = dict(
        token_id=SecretStr("ak-1"),
        token_secret=SecretStr("as-1"),
        source="env",
        profile=None,
    )
    base.update(overrides)
    return ModalCredentials(**base)  # type: ignore[arg-type]


class TestModalCredentials:
    def test_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        creds = _creds()
        with pytest.raises(FrozenInstanceError):
            creds.source = "toml"  # type: ignore[misc]

    def test_repr_does_not_leak_secret(self) -> None:
        creds = _creds(
            token_id=SecretStr("ak-secret-id"),
            token_secret=SecretStr("as-secret-value"),
        )
        text = repr(creds)
        assert "ak-secret-id" not in text
        assert "as-secret-value" not in text

    def test_describe_env_source(self) -> None:
        assert _creds(source="env").describe() == "loaded from MODAL_TOKEN_ID env var"

    def test_describe_toml_source_includes_profile(self, tmp_path: Path) -> None:
        creds = _creds(
            source="toml", profile="staging", config_path=tmp_path / "modal.toml"
        )
        assert creds.describe() == (
            f"loaded from {tmp_path / 'modal.toml'} at profile 'staging'"
        )

    def test_describe_injected_source(self) -> None:
        assert _creds(source="injected").describe() == "injected by caller (test/fake)"


class TestCredentialSourceResolveEnv:
    def test_env_pair_takes_priority(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, token_id="ak-env", token_secret="as-env")
        creds = CredentialSource.resolve(settings)
        assert creds.source == "env"
        assert creds.profile is None
        assert creds.token_id.get_secret_value() == "ak-env"
        assert creds.token_secret.get_secret_value() == "as-env"


class TestCredentialSourceResolveToml:
    def test_toml_fallback_when_no_env_tokens(self, tmp_path: Path) -> None:
        toml_text = '[default]\ntoken_id = "ak-toml"\ntoken_secret = "as-toml"\n'
        settings = _settings(tmp_path, modal_config_text=toml_text)
        creds = CredentialSource.resolve(settings)
        assert creds.source == "toml"
        assert creds.profile == "default"
        assert creds.token_id.get_secret_value() == "ak-toml"
        assert creds.token_secret.get_secret_value() == "as-toml"

    def test_toml_named_profile(self, tmp_path: Path) -> None:
        toml_text = (
            "[default]\n"
            'token_id = "ak-default"\n'
            'token_secret = "as-default"\n'
            "[staging]\n"
            'token_id = "ak-staging"\n'
            'token_secret = "as-staging"\n'
        )
        settings = _settings(tmp_path, modal_config_text=toml_text, profile="staging")
        creds = CredentialSource.resolve(settings)
        assert creds.source == "toml"
        assert creds.profile == "staging"
        assert creds.token_id.get_secret_value() == "ak-staging"


class TestCredentialSourceFailureModes:
    def test_missing_toml_and_no_env_raises(self, tmp_path: Path) -> None:
        # Settings construction requires modal.toml to exist; build with placeholder,
        # then delete to test the post-Settings resolution path.
        settings = _settings(tmp_path)
        settings.modal_config_path.unlink()
        with pytest.raises(CredentialError, match="no Modal credentials"):
            CredentialSource.resolve(settings)

    def test_malformed_toml_raises(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, modal_config_text="this is not [valid toml")
        with pytest.raises(CredentialError, match="could not parse"):
            CredentialSource.resolve(settings)

    def test_toml_missing_token_id_raises(self, tmp_path: Path) -> None:
        settings = _settings(
            tmp_path, modal_config_text='[default]\ntoken_secret = "as-only"\n'
        )
        with pytest.raises(CredentialError, match="token_id"):
            CredentialSource.resolve(settings)

    def test_toml_profile_not_found_raises(self, tmp_path: Path) -> None:
        toml_text = '[default]\ntoken_id = "x"\ntoken_secret = "y"\n'
        settings = _settings(tmp_path, modal_config_text=toml_text, profile="missing")
        with pytest.raises(CredentialError, match="profile 'missing'"):
            CredentialSource.resolve(settings)


class TestCredentialSourceHalfPairEnv:
    """Setting only one of MODAL_TOKEN_ID / SECRET must NOT fall through to TOML.

    ``Settings`` itself rejects half-pair env input at validation time, but
    ``CredentialSource.resolve`` is the documented resolver and must be
    self-protective: a hand-constructed ``Settings`` (e.g. via
    ``model_construct`` in tests, or future Settings refactors) must not
    silently produce TOML-sourced credentials when the operator partially
    configured env auth.
    """

    def _half_pair_settings(
        self,
        tmp_path: Path,
        *,
        token_id: str | None,
        token_secret: str | None,
    ) -> Settings:
        # Build a complete Settings, then bypass validation by constructing a
        # mutated copy via ``model_construct`` so we can test the
        # CredentialSource branch directly.
        base = _settings(
            tmp_path,
            token_id="ak-env",
            token_secret="as-env",
            modal_config_text=(
                '[default]\ntoken_id = "ak-toml"\ntoken_secret = "as-toml"\n'
            ),
        )
        return base.model_copy(
            update={
                "modal_token_id": (
                    SecretStr(token_id) if token_id is not None else None
                ),
                "modal_token_secret": (
                    SecretStr(token_secret) if token_secret is not None else None
                ),
            }
        )

    def test_env_id_without_secret_raises(self, tmp_path: Path) -> None:
        settings = self._half_pair_settings(
            tmp_path, token_id="ak-env", token_secret=None
        )
        with pytest.raises(
            CredentialError,
            match=r"MODAL_TOKEN_ID is set but MODAL_TOKEN_SECRET is missing",
        ):
            CredentialSource.resolve(settings)

    def test_env_secret_without_id_raises(self, tmp_path: Path) -> None:
        settings = self._half_pair_settings(
            tmp_path, token_id=None, token_secret="as-env"
        )
        with pytest.raises(
            CredentialError,
            match=r"MODAL_TOKEN_SECRET is set but MODAL_TOKEN_ID is missing",
        ):
            CredentialSource.resolve(settings)


class TestCredentialSourceTomlTypeValidation:
    """TOML token fields must be strings; non-string values fail fast."""

    def test_int_token_id_raises(self, tmp_path: Path) -> None:
        # Missing quotes around the token id — TOML parses it as int.
        settings = _settings(
            tmp_path,
            modal_config_text='[default]\ntoken_id = 123\ntoken_secret = "as"\n',
        )
        with pytest.raises(
            CredentialError, match=r"token_id must be a string, got int"
        ):
            CredentialSource.resolve(settings)

    def test_int_token_secret_raises(self, tmp_path: Path) -> None:
        settings = _settings(
            tmp_path,
            modal_config_text='[default]\ntoken_id = "ak"\ntoken_secret = 456\n',
        )
        with pytest.raises(
            CredentialError, match=r"token_secret must be a string, got int"
        ):
            CredentialSource.resolve(settings)


class TestCredentialSourceInjected:
    def test_inject_bypasses_resolution(self) -> None:
        injected = ModalCredentials(
            token_id=SecretStr("ak-inject"),
            token_secret=SecretStr("as-inject"),
            source="injected",
            profile=None,
        )
        assert injected.source == "injected"
        assert injected.describe() == "injected by caller (test/fake)"
