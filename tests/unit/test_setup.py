"""Unit tests for modal_mcp.setup non-interactive setup generation."""

from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path

import pytest

from modal_mcp.__main__ import build_parser, main
from modal_mcp.domain.refs import parse_signing_keys
from modal_mcp.setup import (
    DEFAULT_SIGNING_KEY_NAME,
    FORBIDDEN_SETUP_ENV_KEYS,
    SetupResult,
    run_setup,
)
from modal_mcp.setup_files import SetupFilesError

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
        """An .env placed before setup is called must not be overwritten."""
        env_file = tmp_path / ".env"
        env_file.write_text("CUSTOM_VAR=my_value\n")

        run_setup(env_file=env_file, secrets_dir=tmp_path / ".secrets")
        assert env_file.read_text() == "CUSTOM_VAR=my_value\n"


# ---------------------------------------------------------------------------
# Symlink refusal (unsafe secret paths)
# ---------------------------------------------------------------------------


class TestSymlinkRefusal:
    def test_refuses_symlink_at_key_path(self, tmp_path: Path) -> None:
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
        """The real file behind the env symlink must not be modified."""
        real_env = tmp_path / "real.env"
        real_env.write_text("ORIGINAL=yes\n")
        link = tmp_path / ".env"
        link.symlink_to(real_env)

        with contextlib.suppress(SetupFilesError):
            run_setup(env_file=link, secrets_dir=tmp_path / ".secrets")

        assert real_env.read_text() == "ORIGINAL=yes\n"

    def test_refuses_dangling_symlink_at_key_path(self, tmp_path: Path) -> None:
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
        link = tmp_path / ".env"
        link.symlink_to(tmp_path / "nowhere.env")

        with pytest.raises(SetupFilesError, match="symlink"):
            run_setup(
                env_file=link,
                secrets_dir=tmp_path / ".secrets",
            )


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
