"""Unit tests for modal_mcp.setup non-interactive setup generation."""

from __future__ import annotations

import contextlib
import io
import os
import stat
from pathlib import Path

import pytest

from modal_mcp.__main__ import build_parser, main
from modal_mcp.domain.refs import parse_signing_keys
from modal_mcp.observability.redact import REDACTION_PLACEHOLDER, redact_string
from modal_mcp.setup import (
    DEFAULT_SIGNING_KEY_NAME,
    DEFAULT_TOKEN_ID_NAME,
    DEFAULT_TOKEN_SECRET_NAME,
    FORBIDDEN_SETUP_ENV_KEYS,
    MODAL_TOML_CREDENTIAL_WARNING,
    CredentialChoiceError,
    ServiceTokenResult,
    SetupResult,
    collect_setup_known_secrets,
    detect_modal_toml,
    require_tty_for_credential_choice,
    run_setup,
    warn_if_modal_toml_present,
    write_service_token_files,
)
from modal_mcp.domain.file_io import SetupFilesError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mode(path: Path) -> int:
    """Return the low 9 permission bits of *path*."""
    return stat.S_IMODE(path.stat().st_mode)


def _make_setup_paths(
    tmp_path: Path,
) -> tuple[Path, Path]:
    """Return (env_file, secrets_dir) rooted under *tmp_path*."""
    return tmp_path / ".env", tmp_path / ".secrets"


# ---------------------------------------------------------------------------
# Happy path: file creation
# ---------------------------------------------------------------------------


class TestRunSetupHappyPath:
    def test_creates_env_file(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert env_file.is_file()

    def test_creates_signing_key_file(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        key_path = secrets_dir / DEFAULT_SIGNING_KEY_NAME
        assert key_path.is_file()

    def test_returns_setup_result(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert isinstance(result, SetupResult)

    def test_result_paths_are_absolute(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result.env_file.is_absolute()
        assert result.signing_key_file.is_absolute()

    def test_result_env_file_matches_requested_path(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result.env_file == env_file.resolve()

    def test_result_signing_key_file_matches_expected_path(
        self, tmp_path: Path
    ) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert (
            result.signing_key_file
            == (secrets_dir / DEFAULT_SIGNING_KEY_NAME).resolve()
        )

    def test_signing_key_created_flag_true_for_new_key(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result.signing_key_created is True

    def test_env_created_flag_true_for_new_env(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result.env_created is True

    def test_creates_secrets_dir(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert secrets_dir.is_dir()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_signing_key_file_mode_is_600(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert _mode(result.signing_key_file) == 0o600

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_secrets_dir_mode_is_700(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert _mode(secrets_dir) == 0o700

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_env_file_mode_is_600(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert _mode(result.env_file) == 0o600


# ---------------------------------------------------------------------------
# .env content: no forbidden tokens, correct entries
# ---------------------------------------------------------------------------


class TestEnvFileContent:
    def _read_env(self, tmp_path: Path) -> str:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        return result.env_file.read_text()

    @pytest.mark.parametrize("forbidden_key", sorted(FORBIDDEN_SETUP_ENV_KEYS))
    def test_contains_no_forbidden_key(
        self, tmp_path: Path, forbidden_key: str
    ) -> None:
        env_text = self._read_env(tmp_path)
        # The key must not appear as a variable assignment in the file.
        assert f"{forbidden_key}=" not in env_text, (
            f"setup-generated .env must not contain {forbidden_key!r}"
        )

    def test_contains_no_modal_token_id(self, tmp_path: Path) -> None:
        env_text = self._read_env(tmp_path)
        assert "MODAL_TOKEN_ID=" not in env_text

    def test_contains_no_modal_token_secret(self, tmp_path: Path) -> None:
        env_text = self._read_env(tmp_path)
        assert "MODAL_TOKEN_SECRET=" not in env_text

    def test_contains_no_modal_environment(self, tmp_path: Path) -> None:
        env_text = self._read_env(tmp_path)
        assert "MODAL_ENVIRONMENT=" not in env_text

    def test_contains_signing_key_file_path(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        env_text = result.env_file.read_text()
        assert "MODAL_MCP_SIGNING_KEY_FILE=" in env_text

    def test_signing_key_file_path_in_env_is_absolute(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        env_text = result.env_file.read_text()
        # Extract the path value from the line
        for line in env_text.splitlines():
            if line.startswith("MODAL_MCP_SIGNING_KEY_FILE="):
                path_value = line.split("=", 1)[1]
                assert Path(path_value).is_absolute(), (
                    "MODAL_MCP_SIGNING_KEY_FILE must be an absolute path"
                )
                break
        else:
            pytest.fail("MODAL_MCP_SIGNING_KEY_FILE not found in .env")

    def test_signing_key_file_path_in_env_matches_result(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        env_text = result.env_file.read_text()
        for line in env_text.splitlines():
            if line.startswith("MODAL_MCP_SIGNING_KEY_FILE="):
                path_value = line.split("=", 1)[1]
                assert Path(path_value) == result.signing_key_file
                break
        else:
            pytest.fail("MODAL_MCP_SIGNING_KEY_FILE not found in .env")

    def test_contains_allowed_origins(self, tmp_path: Path) -> None:
        env_text = self._read_env(tmp_path)
        assert "MODAL_MCP_ALLOWED_ORIGINS=" in env_text

    def test_allowed_origins_value_is_nonempty(self, tmp_path: Path) -> None:
        env_text = self._read_env(tmp_path)
        for line in env_text.splitlines():
            if line.startswith("MODAL_MCP_ALLOWED_ORIGINS="):
                value = line.split("=", 1)[1].strip()
                assert value, "MODAL_MCP_ALLOWED_ORIGINS must not be empty"
                break
        else:
            pytest.fail("MODAL_MCP_ALLOWED_ORIGINS not found in .env")


# ---------------------------------------------------------------------------
# Signing key format and validity
# ---------------------------------------------------------------------------


class TestSigningKeyFormat:
    def test_signing_key_has_kid_colon_hex_format(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        raw = result.signing_key_file.read_text().strip()
        assert ":" in raw, "signing key must be in kid:hex format"
        kid, hex_key = raw.split(":", 1)
        assert kid, "kid must not be empty"
        assert hex_key, "hex key material must not be empty"

    def test_signing_key_hex_is_valid(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        raw = result.signing_key_file.read_text().strip()
        _, hex_key = raw.split(":", 1)
        # Should be decodable as hex
        key_bytes = bytes.fromhex(hex_key)
        assert len(key_bytes) == 32, "signing key must be 32 bytes (256-bit)"

    def test_signing_key_parseable_by_parse_signing_keys(self, tmp_path: Path) -> None:
        """Key material must be accepted by the domain codec."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        raw = result.signing_key_file.read_text().strip()
        parsed = parse_signing_keys(raw)
        assert len(parsed) == 1
        kid, key_bytes = parsed[0]
        assert kid
        assert len(key_bytes) == 32

    def test_two_runs_produce_different_keys(self, tmp_path: Path) -> None:
        """Each invocation must generate a distinct key."""
        env_file1, secrets_dir1 = tmp_path / ".env1", tmp_path / ".secrets1"
        env_file2, secrets_dir2 = tmp_path / ".env2", tmp_path / ".secrets2"
        result1 = run_setup(env_file=env_file1, secrets_dir=secrets_dir1)
        result2 = run_setup(env_file=env_file2, secrets_dir=secrets_dir2)
        key1 = result1.signing_key_file.read_text().strip()
        key2 = result2.signing_key_file.read_text().strip()
        assert key1 != key2, "independently generated keys must not be identical"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_preserves_existing_signing_key(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        # First run: create key
        result1 = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        original_key = result1.signing_key_file.read_text()
        # Second run: key must be preserved
        result2 = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result2.signing_key_file.read_text() == original_key

    def test_preserves_existing_env(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        # First run: create .env
        result1 = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        original_env = result1.env_file.read_text()
        # Second run: .env must be preserved
        result2 = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result2.env_file.read_text() == original_env

    def test_signing_key_created_flag_false_for_existing(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result.signing_key_created is False

    def test_env_created_flag_false_for_existing(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result.env_created is False

    def test_preserves_preexisting_key_written_externally(self, tmp_path: Path) -> None:
        """A key placed before setup is called must not be overwritten."""
        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir(mode=0o700)
        key_path = secrets_dir / DEFAULT_SIGNING_KEY_NAME
        original = (
            "mykey:aabbccdd112233445566778899001122334455667788990011223344556677889900"
        )
        key_path.write_text(original)

        run_setup(env_file=tmp_path / ".env", secrets_dir=secrets_dir)
        assert key_path.read_text() == original

    def test_preserves_preexisting_env_written_externally(self, tmp_path: Path) -> None:
        """An .env placed before setup is called must not be overwritten;
        missing modal-mcp keys are appended."""
        env_file = tmp_path / ".env"
        env_file.write_text("CUSTOM_VAR=my_value\n")

        result = run_setup(env_file=env_file, secrets_dir=tmp_path / ".secrets")
        text = env_file.read_text()
        assert "CUSTOM_VAR=my_value" in text
        assert "MODAL_MCP_SIGNING_KEY_FILE=" in text
        assert "MODAL_MCP_ALLOWED_ORIGINS=" in text
        assert result.env_created is True

    def test_appends_only_missing_keys_to_existing_env(self, tmp_path: Path) -> None:
        """Existing modal-mcp keys are preserved; only missing ones are appended."""
        env_file = tmp_path / ".env"
        env_file.write_text("MODAL_MCP_ALLOWED_ORIGINS=http://localhost:3000\n")

        result = run_setup(env_file=env_file, secrets_dir=tmp_path / ".secrets")
        text = env_file.read_text()
        assert "MODAL_MCP_ALLOWED_ORIGINS=http://localhost:3000" in text
        assert "MODAL_MCP_SIGNING_KEY_FILE=" in text
        # Only one signing key line should exist
        assert text.count("MODAL_MCP_SIGNING_KEY_FILE=") == 1
        assert result.env_created is True

    def test_does_not_modify_env_when_all_keys_present(self, tmp_path: Path) -> None:
        """When all modal-mcp keys exist, the .env file is left unchanged."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "CUSTOM_VAR=my_value\n"
            "MODAL_MCP_SIGNING_KEY_FILE=/tmp/key.txt\n"
            "MODAL_MCP_ALLOWED_ORIGINS=http://localhost:3000\n"
        )

        result = run_setup(env_file=env_file, secrets_dir=tmp_path / ".secrets")
        text = env_file.read_text()
        assert text == (
            "CUSTOM_VAR=my_value\n"
            "MODAL_MCP_SIGNING_KEY_FILE=/tmp/key.txt\n"
            "MODAL_MCP_ALLOWED_ORIGINS=http://localhost:3000\n"
        )
        assert result.env_created is False


# ---------------------------------------------------------------------------
# Symlink refusal (unsafe secret paths)
# ---------------------------------------------------------------------------


class TestSymlinkRefusal:
    @staticmethod
    def _skip_if_no_symlinks(tmp_path: Path) -> None:
        """Skip symlink tests on platforms that cannot create symlinks."""
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")

    def test_refuses_symlink_at_key_path(self, tmp_path: Path) -> None:
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        """write_secret must refuse to overwrite via a symlink."""
        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir(mode=0o700)
        real_key = tmp_path / "real_key.txt"
        real_key.write_text("original")
        link = secrets_dir / DEFAULT_SIGNING_KEY_NAME
        link.symlink_to(real_key)

        with pytest.raises(SetupFilesError, match="symlink"):
            run_setup(
                env_file=tmp_path / ".env",
                secrets_dir=secrets_dir,
            )

    def test_key_symlink_target_untouched(self, tmp_path: Path) -> None:
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        """The real file behind the symlink must not be modified."""
        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir(mode=0o700)
        real_key = tmp_path / "real_key.txt"
        real_key.write_text("original")
        link = secrets_dir / DEFAULT_SIGNING_KEY_NAME
        link.symlink_to(real_key)

        with contextlib.suppress(SetupFilesError):
            run_setup(env_file=tmp_path / ".env", secrets_dir=secrets_dir)

        assert real_key.read_text() == "original"

    def test_refuses_symlink_at_env_path(self, tmp_path: Path) -> None:
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        """`_write_env_idempotent` must refuse to write through a symlink."""
        real_env = tmp_path / "real.env"
        real_env.write_text("ORIGINAL=yes\n")
        link = tmp_path / ".env"
        link.symlink_to(real_env)

        with pytest.raises(SetupFilesError, match="symlink"):
            run_setup(
                env_file=link,
                secrets_dir=tmp_path / ".secrets",
            )

    def test_env_symlink_target_untouched(self, tmp_path: Path) -> None:
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        """The real file behind the env symlink must not be modified."""
        real_env = tmp_path / "real.env"
        real_env.write_text("ORIGINAL=yes\n")
        link = tmp_path / ".env"
        link.symlink_to(real_env)

        with contextlib.suppress(SetupFilesError):
            run_setup(env_file=link, secrets_dir=tmp_path / ".secrets")

        assert real_env.read_text() == "ORIGINAL=yes\n"

    def test_refuses_dangling_symlink_at_key_path(self, tmp_path: Path) -> None:
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        """A dangling symlink (target absent) must also be refused."""
        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir(mode=0o700)
        link = secrets_dir / DEFAULT_SIGNING_KEY_NAME
        link.symlink_to(tmp_path / "nowhere.txt")

        with pytest.raises(SetupFilesError, match="symlink"):
            run_setup(
                env_file=tmp_path / ".env",
                secrets_dir=secrets_dir,
            )

    def test_refuses_dangling_symlink_at_env_path(self, tmp_path: Path) -> None:
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        link = tmp_path / ".env"
        link.symlink_to(tmp_path / "nowhere.env")

        with pytest.raises(SetupFilesError, match="symlink"):
            run_setup(
                env_file=link,
                secrets_dir=tmp_path / ".secrets",
            )


# ---------------------------------------------------------------------------
# Force semantics
# ---------------------------------------------------------------------------


class TestForceSemantics:
    """Tests for the force=True / force=False parameter of run_setup."""

    # ------------------------------------------------------------------
    # force=False (default) — preserves pre-existing content
    # ------------------------------------------------------------------

    def test_force_false_preserves_existing_env_content(self, tmp_path: Path) -> None:
        """Pre-existing .env content is preserved;
        missing modal-mcp keys are appended when force=False."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        env_file.write_text("CUSTOM_VAR=preserved_content\n")
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir, force=False)
        text = env_file.read_text()
        assert "CUSTOM_VAR=preserved_content" in text
        assert "MODAL_MCP_SIGNING_KEY_FILE=" in text
        assert result.env_created is True

    def test_force_false_preserves_existing_signing_key_content(
        self, tmp_path: Path
    ) -> None:
        """Pre-existing signing key must not be overwritten when force=False."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        secrets_dir.mkdir(mode=0o700)
        key_path = secrets_dir / DEFAULT_SIGNING_KEY_NAME
        original_key = "mykey:" + "aa" * 32
        key_path.write_text(original_key)
        run_setup(env_file=env_file, secrets_dir=secrets_dir, force=False)
        assert key_path.read_text() == original_key

    def test_force_false_env_created_flag_true_when_keys_appended(
        self, tmp_path: Path
    ) -> None:
        """env_created is True when missing modal-mcp keys are appended
        to an existing file."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        env_file.write_text("EXISTING=1\n")
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir, force=False)
        assert result.env_created is True

    def test_force_false_signing_key_created_flag_false_for_existing(
        self, tmp_path: Path
    ) -> None:
        """signing_key_created must be False when an existing key was preserved."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        secrets_dir.mkdir(mode=0o700)
        key_path = secrets_dir / DEFAULT_SIGNING_KEY_NAME
        key_path.write_text("mykey:" + "bb" * 32)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir, force=False)
        assert result.signing_key_created is False

    # ------------------------------------------------------------------
    # force=True — replaces existing files with fresh content
    # ------------------------------------------------------------------

    def test_force_true_replaces_existing_signing_key_content(
        self, tmp_path: Path
    ) -> None:
        """force=True must generate a new key even when one already exists."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result1 = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        original_key = result1.signing_key_file.read_text().strip()
        result2 = run_setup(env_file=env_file, secrets_dir=secrets_dir, force=True)
        new_key = result2.signing_key_file.read_text().strip()
        # A freshly generated 256-bit key is astronomically unlikely to collide.
        assert new_key != original_key

    def test_force_true_replaces_existing_env_content(self, tmp_path: Path) -> None:
        """force=True must overwrite a pre-existing .env with generated content."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        env_file.write_text("OLD_CUSTOM_VAR=old_value\n")
        run_setup(env_file=env_file, secrets_dir=secrets_dir, force=True)
        new_content = env_file.read_text()
        assert "OLD_CUSTOM_VAR=old_value" not in new_content
        assert "MODAL_MCP_SIGNING_KEY_FILE=" in new_content

    def test_force_true_signing_key_created_flag_true_when_existing(
        self, tmp_path: Path
    ) -> None:
        """signing_key_created must be True when force=True replaced an existing key."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir, force=True)
        assert result.signing_key_created is True

    def test_force_true_env_created_flag_true_when_existing(
        self, tmp_path: Path
    ) -> None:
        """env_created must be True when force=True replaced an existing .env."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir, force=True)
        assert result.env_created is True

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_force_true_signing_key_mode_remains_600(self, tmp_path: Path) -> None:
        """After force=True replacement the signing key must still be mode 0600."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir, force=True)
        assert _mode(result.signing_key_file) == 0o600

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_force_true_env_file_mode_remains_600(self, tmp_path: Path) -> None:
        """After force=True replacement the .env file must still be mode 0600."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir, force=True)
        assert _mode(result.env_file) == 0o600

    def test_force_true_no_modal_token_in_env(self, tmp_path: Path) -> None:
        """Forbidden env keys must not appear in the regenerated .env."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        env_file.write_text("MODAL_TOKEN_ID=should-be-gone\n")
        run_setup(env_file=env_file, secrets_dir=secrets_dir, force=True)
        new_content = env_file.read_text()
        for key in FORBIDDEN_SETUP_ENV_KEYS:
            assert f"{key}=" not in new_content

    def test_force_true_no_modal_environment_in_env(self, tmp_path: Path) -> None:
        """MODAL_ENVIRONMENT must not appear in the regenerated .env."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir, force=True)
        assert "MODAL_ENVIRONMENT=" not in result.env_file.read_text()

    def test_force_true_signing_key_material_not_in_env(self, tmp_path: Path) -> None:
        """The raw signing key hex must not appear in the regenerated .env."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir, force=True)
        raw_key = result.signing_key_file.read_text().strip()
        _, hex_part = raw_key.split(":", 1)
        env_text = result.env_file.read_text()
        assert hex_part not in env_text


# ---------------------------------------------------------------------------
# Explicit custom paths: combined env_file + secrets_dir
# ---------------------------------------------------------------------------


class TestExplicitCustomPaths:
    """Verify that run_setup respects both env_file and secrets_dir simultaneously."""

    def test_both_custom_paths_files_created_at_exact_locations(
        self, tmp_path: Path
    ) -> None:
        """Files must land exactly at the requested paths, not CWD defaults."""
        env_file = tmp_path / "myconfig" / "server.env"
        secrets_dir = tmp_path / "mysecrets"
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result.env_file == env_file.resolve()
        assert result.env_file.is_file()
        assert result.signing_key_file.parent == secrets_dir.resolve()
        assert result.signing_key_file.is_file()

    def test_env_file_content_references_custom_key_path(self, tmp_path: Path) -> None:
        """The .env generated must reference the signing key at the custom path."""
        env_file = tmp_path / "config" / "local.env"
        secrets_dir = tmp_path / "private"
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        env_text = result.env_file.read_text()
        for line in env_text.splitlines():
            if line.startswith("MODAL_MCP_SIGNING_KEY_FILE="):
                path_value = Path(line.split("=", 1)[1])
                assert path_value == result.signing_key_file
                break
        else:
            pytest.fail("MODAL_MCP_SIGNING_KEY_FILE not found in .env")

    def test_gitignore_placed_in_env_file_parent(self, tmp_path: Path) -> None:
        """The .gitignore must be placed next to env_file, not in secrets_dir."""
        env_file = tmp_path / "subdir" / ".env"
        secrets_dir = tmp_path / "othersecrets"
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        expected_gitignore = tmp_path / "subdir" / ".gitignore"
        assert expected_gitignore.is_file()
        # gitignore must NOT appear in the secrets directory
        assert not (secrets_dir / ".gitignore").exists()


# ---------------------------------------------------------------------------
# .gitignore update
# ---------------------------------------------------------------------------


class TestGitignoreUpdate:
    """.gitignore in the setup root must be created/updated idempotently."""

    def test_gitignore_created_in_setup_root(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert (tmp_path / ".gitignore").is_file()

    def test_gitignore_contains_env_entry(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        lines = (tmp_path / ".gitignore").read_text().splitlines()
        stripped = {line.strip() for line in lines}
        assert ".env" in stripped

    def test_gitignore_contains_env_glob_entry(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        lines = (tmp_path / ".gitignore").read_text().splitlines()
        stripped = {line.strip() for line in lines}
        assert ".env.*" in stripped

    def test_gitignore_contains_secrets_dir_entry(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        lines = (tmp_path / ".gitignore").read_text().splitlines()
        stripped = {line.strip() for line in lines}
        assert ".secrets/" in stripped

    def test_gitignore_second_run_does_not_duplicate_entries(
        self, tmp_path: Path
    ) -> None:
        """Running setup twice must not add duplicate entries."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        lines = [
            line.strip()
            for line in (tmp_path / ".gitignore").read_text().splitlines()
            if line.strip()
        ]
        assert lines.count(".env") == 1
        assert lines.count(".env.*") == 1
        assert lines.count(".secrets/") == 1

    def test_gitignore_updated_flag_true_on_first_run(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result.gitignore_updated is True

    def test_gitignore_updated_flag_false_when_all_entries_present(
        self, tmp_path: Path
    ) -> None:
        """gitignore_updated must be False when no new entries were added."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result.gitignore_updated is False

    def test_gitignore_appends_to_existing_content(self, tmp_path: Path) -> None:
        """Existing gitignore content must be preserved when entries are appended."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\ndist/\n")
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        text = gitignore.read_text()
        assert "node_modules/" in text
        assert "dist/" in text
        stripped = {line.strip() for line in text.splitlines()}
        assert ".env" in stripped

    def test_gitignore_idempotent_when_entries_already_present(
        self, tmp_path: Path
    ) -> None:
        """If all setup entries are pre-populated, the file must not be modified."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        gitignore = tmp_path / ".gitignore"
        original = ".env\n.env.*\n.secrets/\n"
        gitignore.write_text(original)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        assert result.gitignore_updated is False
        # Content must not be duplicated
        text = gitignore.read_text()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        assert lines.count(".env") == 1

    def test_gitignore_force_true_does_not_duplicate_entries(
        self, tmp_path: Path
    ) -> None:
        """force=True must not duplicate gitignore entries on repeated runs."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        run_setup(env_file=env_file, secrets_dir=secrets_dir)
        run_setup(env_file=env_file, secrets_dir=secrets_dir, force=True)
        lines = [
            line.strip()
            for line in (tmp_path / ".gitignore").read_text().splitlines()
            if line.strip()
        ]
        assert lines.count(".env") == 1
        assert lines.count(".env.*") == 1
        assert lines.count(".secrets/") == 1


# ---------------------------------------------------------------------------
# Custom key name and directory
# ---------------------------------------------------------------------------


class TestCustomPaths:
    def test_custom_signing_key_name(self, tmp_path: Path) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(
            env_file=env_file,
            secrets_dir=secrets_dir,
            signing_key_name="my-key.bin",
        )
        assert result.signing_key_file.name == "my-key.bin"
        assert result.signing_key_file.is_file()

    def test_custom_secrets_dir(self, tmp_path: Path) -> None:
        custom_dir = tmp_path / "vault"
        result = run_setup(
            env_file=tmp_path / ".env",
            secrets_dir=custom_dir,
        )
        assert result.signing_key_file.parent == custom_dir.resolve()

    def test_custom_env_file_path(self, tmp_path: Path) -> None:
        env_file = tmp_path / "config" / "local.env"
        result = run_setup(
            env_file=env_file,
            secrets_dir=tmp_path / ".secrets",
        )
        assert result.env_file == env_file.resolve()
        assert result.env_file.is_file()


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestSetupCLI:
    def test_setup_yes_flag_registered_in_parser(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup", "--yes"])
        assert args.yes is True

    def test_setup_without_yes_defaults_to_false(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup"])
        assert args.yes is False

    def test_setup_without_yes_returns_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        result = main(["setup"])
        assert result == 0

    def test_setup_without_yes_prints_instructions(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["setup"])
        out = capsys.readouterr().out
        assert out.strip()

    def test_setup_yes_returns_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = main(["setup", "--yes"])
        assert result == 0

    def test_setup_yes_creates_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["setup", "--yes"])
        assert (tmp_path / ".env").is_file()

    def test_setup_yes_creates_signing_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["setup", "--yes"])
        assert (tmp_path / ".secrets" / DEFAULT_SIGNING_KEY_NAME).is_file()

    def test_setup_yes_prints_paths(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["setup", "--yes"])
        out = capsys.readouterr().out
        assert "Signing key" in out
        assert "Env file" in out

    def test_setup_yes_prints_startup_command(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["setup", "--yes"])
        out = capsys.readouterr().out
        assert "modal-mcp doctor --env-file" in out
        assert "modal-mcp run --env-file" in out

    def test_setup_yes_idempotent_via_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["setup", "--yes"]) == 0
        assert main(["setup", "--yes"]) == 0

    def test_setup_yes_symlink_returns_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        monkeypatch.chdir(tmp_path)
        # Place a dangling symlink at the default .env location
        env_link = tmp_path / ".env"
        env_link.symlink_to(tmp_path / "nowhere.env")
        result = main(["setup", "--yes"])
        assert result == 1

    def test_setup_yes_env_contains_no_modal_token_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["setup", "--yes"])
        env_text = (tmp_path / ".env").read_text()
        assert "MODAL_TOKEN_ID=" not in env_text

    def test_setup_yes_env_contains_no_modal_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["setup", "--yes"])
        env_text = (tmp_path / ".env").read_text()
        assert "MODAL_ENVIRONMENT=" not in env_text


# ---------------------------------------------------------------------------
# Modal TOML credential detection
# ---------------------------------------------------------------------------


class TestDetectModalToml:
    def test_returns_true_when_toml_exists(self, tmp_path: Path) -> None:
        toml = tmp_path / ".modal.toml"
        toml.write_text("[modal]\ntoken_id = 'ak-test'\n")
        assert detect_modal_toml(toml) is True

    def test_returns_false_when_toml_absent(self, tmp_path: Path) -> None:
        missing = tmp_path / ".modal.toml"
        assert detect_modal_toml(missing) is False

    def test_returns_false_for_directory(self, tmp_path: Path) -> None:
        """A directory at the path must not be treated as an existing toml."""
        as_dir = tmp_path / ".modal.toml"
        as_dir.mkdir()
        assert detect_modal_toml(as_dir) is False

    def test_returns_false_for_dangling_symlink(self, tmp_path: Path) -> None:
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        link = tmp_path / ".modal.toml"
        link.symlink_to(tmp_path / "nowhere")
        # is_file() returns False for dangling symlinks
        assert detect_modal_toml(link) is False

    def test_tilde_path_expanded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit tilde-prefixed path is expanded before checking."""
        monkeypatch.setenv("HOME", str(tmp_path))
        toml = tmp_path / ".modal.toml"
        toml.write_text("[modal]\n")
        # Use tilde path
        assert detect_modal_toml(Path("~/.modal.toml")) is True


# ---------------------------------------------------------------------------
# Modal TOML credential warning
# ---------------------------------------------------------------------------


class TestWarnIfModalTomlPresent:
    def test_prints_warning_when_toml_exists(self, tmp_path: Path) -> None:
        toml = tmp_path / ".modal.toml"
        toml.write_text("[modal]\n")
        buf = io.StringIO()
        result = warn_if_modal_toml_present(modal_toml_path=toml, out=buf)
        assert result is True
        assert "Existing Modal credentials" in buf.getvalue()

    def test_warning_contains_recommendation(self, tmp_path: Path) -> None:
        toml = tmp_path / ".modal.toml"
        toml.write_text("[modal]\n")
        buf = io.StringIO()
        warn_if_modal_toml_present(modal_toml_path=toml, out=buf)
        text = buf.getvalue()
        # The warning must guide the user toward service-user tokens
        assert "service-user" in text.lower() or "service" in text.lower()

    def test_returns_false_when_toml_absent(self, tmp_path: Path) -> None:
        missing = tmp_path / ".modal.toml"
        buf = io.StringIO()
        result = warn_if_modal_toml_present(modal_toml_path=missing, out=buf)
        assert result is False
        assert buf.getvalue() == ""

    def test_no_output_when_toml_absent(self, tmp_path: Path) -> None:
        missing = tmp_path / ".modal.toml"
        buf = io.StringIO()
        warn_if_modal_toml_present(modal_toml_path=missing, out=buf)
        assert buf.getvalue() == ""

    def test_warning_constant_contains_credential_phrase(self) -> None:
        """The constant itself must include the required content-check phrase."""
        assert "Existing Modal credentials" in MODAL_TOML_CREDENTIAL_WARNING

    def test_warning_constant_mentions_home_modal_toml(self) -> None:
        assert "~/.modal.toml" in MODAL_TOML_CREDENTIAL_WARNING


# ---------------------------------------------------------------------------
# Non-TTY credential choice guard
# ---------------------------------------------------------------------------


class _FakeTTY(io.StringIO):
    """Fake TTY stream — isatty() always returns True."""

    def isatty(self) -> bool:  # type: ignore[override]
        return True


class _FakeNonTTY(io.StringIO):
    """Fake non-TTY stream — isatty() always returns False."""

    def isatty(self) -> bool:  # type: ignore[override]
        return False


class TestRequireTTYForCredentialChoice:
    def test_raises_for_non_tty(self) -> None:
        with pytest.raises(CredentialChoiceError):
            require_tty_for_credential_choice(stdin=_FakeNonTTY())

    def test_does_not_raise_for_tty(self) -> None:
        # Must not raise when stdin is a TTY
        require_tty_for_credential_choice(stdin=_FakeTTY())

    def test_error_is_value_error_subclass(self) -> None:
        with pytest.raises(ValueError):
            require_tty_for_credential_choice(stdin=_FakeNonTTY())

    def test_error_message_mentions_tty(self) -> None:
        with pytest.raises(CredentialChoiceError, match="TTY"):
            require_tty_for_credential_choice(stdin=_FakeNonTTY())

    def test_error_message_mentions_non_tty(self) -> None:
        with pytest.raises(CredentialChoiceError, match=r"[Nn]on-TTY"):
            require_tty_for_credential_choice(stdin=_FakeNonTTY())

    def test_stream_without_isatty_treated_as_non_tty(self) -> None:
        """A stream that lacks isatty() must be treated as non-interactive."""

        class _NoIsatty:
            pass

        with pytest.raises(CredentialChoiceError):
            require_tty_for_credential_choice(stdin=_NoIsatty())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# File-backed service-user token storage
# ---------------------------------------------------------------------------


class TestWriteServiceTokenFiles:
    def test_creates_token_id_file(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="secret-test",
            secrets_dir=tmp_path / ".secrets",
        )
        assert result.token_id_file.is_file()

    def test_creates_token_secret_file(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="secret-test",
            secrets_dir=tmp_path / ".secrets",
        )
        assert result.token_secret_file.is_file()

    def test_returns_service_token_result(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="secret-test",
            secrets_dir=tmp_path / ".secrets",
        )
        assert isinstance(result, ServiceTokenResult)

    def test_token_id_file_contains_token_id(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-myid",
            token_secret="sk-mysecret",
            secrets_dir=tmp_path / ".secrets",
        )
        assert result.token_id_file.read_text() == "ak-myid"

    def test_token_secret_file_contains_token_secret(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-myid",
            token_secret="sk-mysecret",
            secrets_dir=tmp_path / ".secrets",
        )
        assert result.token_secret_file.read_text() == "sk-mysecret"

    def test_token_id_created_flag_true_for_new_file(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="sk-test",
            secrets_dir=tmp_path / ".secrets",
        )
        assert result.token_id_created is True

    def test_token_secret_created_flag_true_for_new_file(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="sk-test",
            secrets_dir=tmp_path / ".secrets",
        )
        assert result.token_secret_created is True

    def test_idempotent_preserves_existing_token_id(self, tmp_path: Path) -> None:
        sd = tmp_path / ".secrets"
        write_service_token_files(
            token_id="ak-orig", token_secret="sk-orig", secrets_dir=sd
        )
        result2 = write_service_token_files(
            token_id="ak-new", token_secret="sk-new", secrets_dir=sd
        )
        # Original values must be preserved
        assert result2.token_id_file.read_text() == "ak-orig"
        assert result2.token_secret_file.read_text() == "sk-orig"

    def test_idempotent_flags_false_on_second_call(self, tmp_path: Path) -> None:
        sd = tmp_path / ".secrets"
        write_service_token_files(
            token_id="ak-test", token_secret="sk-test", secrets_dir=sd
        )
        result2 = write_service_token_files(
            token_id="ak-test", token_secret="sk-test", secrets_dir=sd
        )
        assert result2.token_id_created is False
        assert result2.token_secret_created is False

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_token_id_file_mode_is_600(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="sk-test",
            secrets_dir=tmp_path / ".secrets",
        )
        assert _mode(result.token_id_file) == 0o600

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_token_secret_file_mode_is_600(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="sk-test",
            secrets_dir=tmp_path / ".secrets",
        )
        assert _mode(result.token_secret_file) == 0o600

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
    def test_secrets_dir_mode_is_700(self, tmp_path: Path) -> None:
        sd = tmp_path / ".secrets"
        write_service_token_files(
            token_id="ak-test", token_secret="sk-test", secrets_dir=sd
        )
        assert _mode(sd) == 0o700

    def test_result_paths_are_absolute(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="sk-test",
            secrets_dir=tmp_path / ".secrets",
        )
        assert result.token_id_file.is_absolute()
        assert result.token_secret_file.is_absolute()

    def test_custom_token_id_name(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="sk-test",
            secrets_dir=tmp_path / ".secrets",
            token_id_name="my-token-id.txt",
        )
        assert result.token_id_file.name == "my-token-id.txt"

    def test_custom_token_secret_name(self, tmp_path: Path) -> None:
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="sk-test",
            secrets_dir=tmp_path / ".secrets",
            token_secret_name="my-token-secret.txt",
        )
        assert result.token_secret_file.name == "my-token-secret.txt"

    def test_refuses_symlink_at_token_id_path(self, tmp_path: Path) -> None:
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        sd = tmp_path / ".secrets"
        sd.mkdir(mode=0o700)
        real = tmp_path / "real-id.txt"
        real.write_text("original")
        link = sd / DEFAULT_TOKEN_ID_NAME
        link.symlink_to(real)
        with pytest.raises(SetupFilesError, match="symlink"):
            write_service_token_files(
                token_id="ak-new", token_secret="sk-new", secrets_dir=sd
            )

    def test_refuses_symlink_at_token_secret_path(self, tmp_path: Path) -> None:
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        sd = tmp_path / ".secrets"
        sd.mkdir(mode=0o700)
        # Write the ID file normally so it doesn't fail there first.
        (sd / DEFAULT_TOKEN_ID_NAME).write_text("ak-test")
        real = tmp_path / "real-secret.txt"
        real.write_text("original")
        link = sd / DEFAULT_TOKEN_SECRET_NAME
        link.symlink_to(real)
        with pytest.raises(SetupFilesError, match="symlink"):
            write_service_token_files(
                token_id="ak-new", token_secret="sk-new", secrets_dir=sd
            )

    def test_token_files_never_written_to_env_file(self, tmp_path: Path) -> None:
        """Token material must not appear in any .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=1\n")
        write_service_token_files(
            token_id="ak-sensitive",
            token_secret="sk-sensitive",
            secrets_dir=tmp_path / ".secrets",
        )
        env_text = env_file.read_text()
        assert "ak-sensitive" not in env_text
        assert "sk-sensitive" not in env_text

    def test_token_values_not_in_modal_environment_key(self, tmp_path: Path) -> None:
        """MODAL_ENVIRONMENT must not be written by write_service_token_files."""
        sd = tmp_path / ".secrets"
        result = write_service_token_files(
            token_id="ak-test",
            token_secret="sk-test",
            secrets_dir=sd,
        )
        # Only two files should exist: the id and secret files
        files_written = {f.name for f in sd.iterdir() if f.is_file()}
        assert "MODAL_ENVIRONMENT" not in files_written
        # And neither file contains that key name
        assert "MODAL_ENVIRONMENT" not in result.token_id_file.read_text()
        assert "MODAL_ENVIRONMENT" not in result.token_secret_file.read_text()


# ---------------------------------------------------------------------------
# Redaction coverage
# ---------------------------------------------------------------------------


class TestSetupRedaction:
    """Setup output must never expose signing keys, token IDs, or token secrets."""

    # ------------------------------------------------------------------
    # collect_setup_known_secrets unit tests
    # ------------------------------------------------------------------

    def test_collect_setup_known_secrets_returns_nonempty_frozenset(
        self, tmp_path: Path
    ) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        known = collect_setup_known_secrets(result)
        assert isinstance(known, frozenset)
        assert len(known) > 0

    def test_collect_setup_known_secrets_contains_full_key_string(
        self, tmp_path: Path
    ) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        raw = result.signing_key_file.read_text().strip()
        known = collect_setup_known_secrets(result)
        assert raw in known

    def test_collect_setup_known_secrets_contains_hex_part(
        self, tmp_path: Path
    ) -> None:
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        raw = result.signing_key_file.read_text().strip()
        _, hex_key = raw.split(":", 1)
        known = collect_setup_known_secrets(result)
        assert hex_key in known

    def test_collect_setup_known_secrets_returns_empty_for_missing_file(
        self, tmp_path: Path
    ) -> None:
        # Build a SetupResult pointing at a non-existent key file.
        fake_result = SetupResult(
            env_file=tmp_path / ".env",
            signing_key_file=tmp_path / ".secrets" / "absent-key.txt",
            signing_key_created=False,
            env_created=False,
        )
        assert collect_setup_known_secrets(fake_result) == frozenset()

    def test_redact_string_hides_signing_key_using_setup_secrets(
        self, tmp_path: Path
    ) -> None:
        """redact_string with collect_setup_known_secrets must strip key material."""
        env_file, secrets_dir = _make_setup_paths(tmp_path)
        result = run_setup(env_file=env_file, secrets_dir=secrets_dir)
        raw = result.signing_key_file.read_text().strip()
        _, hex_key = raw.split(":", 1)
        known = collect_setup_known_secrets(result)

        # Simulate a string that accidentally contains the key.
        leaked_text = f"debug: signing_key_value={hex_key}"
        redacted = redact_string(leaked_text, known_secrets=known)

        assert hex_key not in redacted
        assert REDACTION_PLACEHOLDER in redacted

    # ------------------------------------------------------------------
    # CLI output — signing key must never appear in stdout / stderr
    # ------------------------------------------------------------------

    def test_setup_stdout_does_not_contain_signing_key_hex(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The hex portion of the signing key must not appear in setup stdout."""
        monkeypatch.chdir(tmp_path)
        main(["setup", "--yes"])
        captured = capsys.readouterr()
        key_path = tmp_path / ".secrets" / DEFAULT_SIGNING_KEY_NAME
        raw = key_path.read_text().strip()
        _, hex_key = raw.split(":", 1)
        assert hex_key not in captured.out
        assert hex_key not in captured.err

    def test_setup_stdout_does_not_contain_full_signing_key_string(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The full kid:hex signing key string must not appear in setup stdout."""
        monkeypatch.chdir(tmp_path)
        main(["setup", "--yes"])
        captured = capsys.readouterr()
        key_path = tmp_path / ".secrets" / DEFAULT_SIGNING_KEY_NAME
        raw = key_path.read_text().strip()
        assert raw not in captured.out
        assert raw not in captured.err

    # ------------------------------------------------------------------
    # CLI output — token ID / secret must never appear in stdout / stderr
    # ------------------------------------------------------------------

    def test_setup_stdout_does_not_contain_token_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """MODAL_TOKEN_ID value must not appear in setup stdout or stderr."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MODAL_TOKEN_ID", "tok_id_must_not_appear_in_setup_output")
        main(["setup", "--yes"])
        captured = capsys.readouterr()
        assert "tok_id_must_not_appear_in_setup_output" not in captured.out
        assert "tok_id_must_not_appear_in_setup_output" not in captured.err

    def test_setup_stdout_does_not_contain_token_secret(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """MODAL_TOKEN_SECRET value must not appear in setup stdout or stderr."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(
            "MODAL_TOKEN_SECRET", "tok_sec_must_not_appear_in_setup_output"
        )
        main(["setup", "--yes"])
        captured = capsys.readouterr()
        assert "tok_sec_must_not_appear_in_setup_output" not in captured.out
        assert "tok_sec_must_not_appear_in_setup_output" not in captured.err

    def test_setup_stderr_does_not_contain_token_id_on_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Token ID must not appear in stderr even when setup exits with an error."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MODAL_TOKEN_ID", "tok_id_must_not_leak_on_setup_error")
        # Trigger an error by placing a symlink at the default .env path.
        env_link = tmp_path / ".env"
        env_link.symlink_to(tmp_path / "nowhere.env")
        main(["setup", "--yes"])
        captured = capsys.readouterr()
        assert "tok_id_must_not_leak_on_setup_error" not in captured.err
        assert "tok_id_must_not_leak_on_setup_error" not in captured.out

    def test_setup_stderr_does_not_contain_token_secret_on_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Token secret must not appear in stderr even when setup exits with error."""
        try:
            target = tmp_path / "target"
            link = tmp_path / "link"
            target.write_text("test")
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink support not available on this platform")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "tok_sec_must_not_leak_on_setup_error")
        env_link = tmp_path / ".env"
        env_link.symlink_to(tmp_path / "nowhere.env")
        main(["setup", "--yes"])
        captured = capsys.readouterr()
        assert "tok_sec_must_not_leak_on_setup_error" not in captured.err
        assert "tok_sec_must_not_leak_on_setup_error" not in captured.out
