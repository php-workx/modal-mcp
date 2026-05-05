"""Unit tests for modal_mcp.doctor partial diagnostics."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import modal_mcp.doctor as doctor_module
from modal_mcp.doctor import (
    CheckStatus,
    DiagnosticItem,
    DiagnosticReport,
    _parse_env_file,
    probe_credentials,
    run_doctor,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

#: Env-var names that could pollute credential probe results between tests.
_CREDENTIAL_ENV_KEYS: frozenset[str] = frozenset(
    {
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "MODAL_TOKEN_ID_FILE",
        "MODAL_TOKEN_SECRET_FILE",
        "MODAL_CONFIG_PATH",
        "MODAL_MCP_SIGNING_KEYS",
        "MODAL_MCP_SIGNING_KEY_FILE",
        "MODAL_MCP_ALLOWED_ORIGINS",
        "MODAL_MCP_READ_ONLY",
        "MODAL_MCP_ENABLED_TOOLSETS",
    }
)


def _missing_import(message: str):
    def missing() -> None:
        raise ImportError(message)

    return missing


@pytest.fixture(autouse=True)
def _clean_credential_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove credential env-vars before each test to ensure isolation."""
    for key in _CREDENTIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _write_env(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _make_token_files(tmp_path: Path) -> tuple[Path, Path]:
    """Return (id_file, secret_file) both written with placeholder content."""
    id_file = tmp_path / "token_id.txt"
    secret_file = tmp_path / "token_secret.txt"
    id_file.write_text("myid", encoding="utf-8")
    secret_file.write_text("mysecret", encoding="utf-8")
    return id_file, secret_file


# ---------------------------------------------------------------------------
# _parse_env_file
# ---------------------------------------------------------------------------


class TestParseEnvFile:
    def test_parses_simple_key_value(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "FOO=bar\n")
        assert _parse_env_file(env) == {"FOO": "bar"}

    def test_ignores_comment_lines(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "# this is a comment\nFOO=bar\n")
        result = _parse_env_file(env)
        assert "# this is a comment" not in result
        assert result["FOO"] == "bar"

    def test_ignores_blank_lines(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "\nFOO=bar\n\n")
        assert _parse_env_file(env) == {"FOO": "bar"}

    def test_strips_double_quotes_from_value(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, 'KEY="quoted value"\n')
        assert _parse_env_file(env)["KEY"] == "quoted value"

    def test_strips_single_quotes_from_value(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "KEY='quoted value'\n")
        assert _parse_env_file(env)["KEY"] == "quoted value"

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        env = tmp_path / "nonexistent.env"
        with pytest.raises(FileNotFoundError):
            _parse_env_file(env)

    def test_ignores_lines_without_equals(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "JUST_A_WORD\nFOO=bar\n")
        result = _parse_env_file(env)
        assert "JUST_A_WORD" not in result
        assert result["FOO"] == "bar"

    def test_value_with_equals_sign_preserved(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "KEY=a=b=c\n")
        assert _parse_env_file(env)["KEY"] == "a=b=c"

    def test_multiple_keys(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "A=1\nB=2\nC=3\n")
        assert _parse_env_file(env) == {"A": "1", "B": "2", "C": "3"}

    def test_strips_export_prefix(self, tmp_path: Path) -> None:
        """'export KEY=value' lines must be parsed like 'KEY=value'."""
        env = tmp_path / ".env"
        _write_env(env, "export FOO=bar\n")
        assert _parse_env_file(env) == {"FOO": "bar"}

    def test_strips_export_prefix_with_quoted_value(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, 'export KEY="quoted value"\n')
        assert _parse_env_file(env)["KEY"] == "quoted value"

    def test_strips_export_prefix_mixed_with_plain_lines(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "export A=1\nB=2\nexport C=3\n")
        assert _parse_env_file(env) == {"A": "1", "B": "2", "C": "3"}


# ---------------------------------------------------------------------------
# probe_credentials
# ---------------------------------------------------------------------------


class TestProbeCredentials:
    def test_finds_credentials_in_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "tsec")
        result = probe_credentials()
        assert result.found is True
        assert result.source == "environ"

    def test_environ_source_requires_both_id_and_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
        # Secret is absent — should NOT match environ source.
        result = probe_credentials()
        assert not (result.found and result.source == "environ")

    def test_finds_credentials_in_env_file(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "MODAL_TOKEN_ID=tid\nMODAL_TOKEN_SECRET=tsec\n")
        result = probe_credentials(env_file=env)
        assert result.found is True
        assert result.source == "env_file"

    def test_env_file_source_requires_both_id_and_secret(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "MODAL_TOKEN_ID=tid\n")  # secret absent
        result = probe_credentials(env_file=env)
        assert not (result.found and result.source == "env_file")

    def test_finds_credentials_via_file_backed_environ(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        id_file, secret_file = _make_token_files(tmp_path)
        monkeypatch.setenv("MODAL_TOKEN_ID_FILE", str(id_file))
        monkeypatch.setenv("MODAL_TOKEN_SECRET_FILE", str(secret_file))
        result = probe_credentials()
        assert result.found is True
        assert result.source == "file_backed"

    def test_file_backed_environ_requires_both_files_exist(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        id_file, _ = _make_token_files(tmp_path)
        monkeypatch.setenv("MODAL_TOKEN_ID_FILE", str(id_file))
        monkeypatch.setenv("MODAL_TOKEN_SECRET_FILE", str(tmp_path / "absent.txt"))
        result = probe_credentials()
        # file_backed via environ should not match (secret file absent)
        assert not (result.found and result.source == "file_backed")

    def test_finds_credentials_via_file_backed_in_env_file(
        self, tmp_path: Path
    ) -> None:
        id_file, secret_file = _make_token_files(tmp_path)
        env = tmp_path / ".env"
        _write_env(
            env,
            f"MODAL_TOKEN_ID_FILE={id_file}\nMODAL_TOKEN_SECRET_FILE={secret_file}\n",
        )
        result = probe_credentials(env_file=env)
        assert result.found is True
        assert result.source == "file_backed"

    def test_finds_credentials_via_modal_toml(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "modal.toml"
        toml_path.write_text("[token]\nid = 'x'\nsecret = 'y'\n", encoding="utf-8")
        result = probe_credentials(modal_config_path=toml_path)
        assert result.found is True
        assert result.source == "modal_toml"
        assert str(toml_path) in result.detail

    def test_reports_not_found_when_no_source(self, tmp_path: Path) -> None:
        result = probe_credentials(
            env_file=tmp_path / "absent.env",
            modal_config_path=tmp_path / "absent.toml",
        )
        assert result.found is False
        assert result.source == "none"

    def test_does_not_modify_os_environ(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """probe_credentials must never write to os.environ."""
        env = tmp_path / ".env"
        _write_env(env, "MODAL_TOKEN_ID=tid\nMODAL_TOKEN_SECRET=tsec\n")
        before = dict(os.environ)
        probe_credentials(env_file=env)
        after = dict(os.environ)
        assert before == after

    def test_environ_takes_priority_over_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_TOKEN_ID", "env-tid")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "env-tsec")
        env = tmp_path / ".env"
        _write_env(env, "MODAL_TOKEN_ID=file-tid\nMODAL_TOKEN_SECRET=file-tsec\n")
        result = probe_credentials(env_file=env)
        assert result.source == "environ"

    def test_detail_nonempty_when_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "tsec")
        result = probe_credentials()
        assert result.detail  # must not be empty when found

    def test_modal_config_path_override_respected(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "custom.toml"
        toml_path.write_text("[token]\n", encoding="utf-8")
        # Absent default path
        result = probe_credentials(
            modal_config_path=toml_path,
        )
        assert result.found is True

    def test_env_file_ignored_when_not_is_file(self, tmp_path: Path) -> None:
        result = probe_credentials(
            env_file=tmp_path / "absent.env",
            modal_config_path=tmp_path / "absent.toml",
        )
        assert result.found is False

    def test_finds_file_backed_credentials_with_export_prefix_and_quotes(
        self, tmp_path: Path
    ) -> None:
        """File-backed paths in .env with export prefix and quoted values are found."""
        id_file, secret_file = _make_token_files(tmp_path)
        env = tmp_path / ".env"
        _write_env(
            env,
            f'export MODAL_TOKEN_ID_FILE="{id_file}"\n'
            f'export MODAL_TOKEN_SECRET_FILE="{secret_file}"\n',
        )
        result = probe_credentials(env_file=env)
        assert result.found is True
        assert result.source == "file_backed"

    def test_finds_file_backed_credentials_with_single_quoted_paths(
        self, tmp_path: Path
    ) -> None:
        """Single-quoted file paths in .env are resolved correctly."""
        id_file, secret_file = _make_token_files(tmp_path)
        env = tmp_path / ".env"
        _write_env(
            env,
            f"MODAL_TOKEN_ID_FILE='{id_file}'\n"
            f"MODAL_TOKEN_SECRET_FILE='{secret_file}'\n",
        )
        result = probe_credentials(env_file=env)
        assert result.found is True
        assert result.source == "file_backed"


# ---------------------------------------------------------------------------
# DiagnosticReport
# ---------------------------------------------------------------------------


class TestDiagnosticReport:
    def _make_report(self, *statuses: CheckStatus) -> DiagnosticReport:
        report = DiagnosticReport()
        for i, status in enumerate(statuses):
            report.items.append(DiagnosticItem(f"check_{i}", status, "msg"))
        return report

    def test_has_failures_true_when_fail_present(self) -> None:
        report = self._make_report(CheckStatus.OK, CheckStatus.FAIL)
        assert report.has_failures is True

    def test_has_failures_false_when_only_ok_and_warn(self) -> None:
        report = self._make_report(CheckStatus.OK, CheckStatus.WARN)
        assert report.has_failures is False

    def test_has_warnings_true_when_warn_present(self) -> None:
        report = self._make_report(CheckStatus.OK, CheckStatus.WARN)
        assert report.has_warnings is True

    def test_has_warnings_false_when_only_ok(self) -> None:
        report = self._make_report(CheckStatus.OK)
        assert report.has_warnings is False

    def test_exit_code_one_on_failure(self) -> None:
        report = self._make_report(CheckStatus.FAIL)
        assert report.exit_code == 1

    def test_exit_code_three_on_warn_only(self) -> None:
        """Warnings without failures must produce exit code 3 (partial-ready)."""
        report = self._make_report(CheckStatus.WARN)
        assert report.exit_code == 3

    def test_exit_code_three_on_mixed_ok_and_warn(self) -> None:
        """Mixed OK + WARN without FAIL must still produce exit code 3."""
        report = self._make_report(CheckStatus.OK, CheckStatus.WARN)
        assert report.exit_code == 3

    def test_exit_code_zero_on_all_ok(self) -> None:
        report = self._make_report(CheckStatus.OK)
        assert report.exit_code == 0

    def test_empty_report_has_no_failures(self) -> None:
        report = DiagnosticReport()
        assert report.has_failures is False

    def test_empty_report_has_no_warnings(self) -> None:
        report = DiagnosticReport()
        assert report.has_warnings is False


# ---------------------------------------------------------------------------
# run_doctor — basic structure
# ---------------------------------------------------------------------------


class TestRunDoctorStructure:
    def test_returns_diagnostic_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        assert isinstance(report, DiagnosticReport)

    def test_items_are_diagnostic_items(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        for item in report.items:
            assert isinstance(item, DiagnosticItem)

    def test_produces_at_least_one_item(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        assert len(report.items) > 0

    def test_item_names_are_nonempty_strings(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        for item in report.items:
            assert isinstance(item.name, str)
            assert item.name

    def test_item_messages_are_nonempty_strings(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        for item in report.items:
            assert isinstance(item.message, str)
            assert item.message


# ---------------------------------------------------------------------------
# run_doctor — import checks
# ---------------------------------------------------------------------------


class TestRunDoctorImportChecks:
    def test_import_modal_mcp_ok_in_test_suite(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "import:modal_mcp")
        assert item.status == CheckStatus.OK

    def test_import_modal_ok_in_test_suite(self, tmp_path: Path) -> None:
        pytest.importorskip("modal")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "import:modal")
        assert item.status == CheckStatus.OK

    def test_import_fail_recorded_for_missing_package(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            doctor_module,
            "_import_modal",
            _missing_import("No module named 'modal'"),
        )
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "import:modal")
        # modal SDK absence is non-fatal: reported as WARN (the server can
        # still start without it being locally importable).
        assert item.status == CheckStatus.WARN

    def test_import_fail_message_contains_package_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            doctor_module,
            "_import_uvicorn",
            _missing_import("No module named 'uvicorn'"),
        )
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "import:uvicorn")
        assert "uvicorn" in item.message

    def test_import_fail_causes_exit_code_one(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            doctor_module,
            "_import_fastmcp",
            _missing_import("No module named 'fastmcp'"),
        )
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        assert report.exit_code == 1

    def test_four_import_checks_present(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        import_names = {i.name for i in report.items if i.name.startswith("import:")}
        assert "import:modal_mcp" in import_names
        assert "import:modal" in import_names
        assert "import:fastmcp" in import_names
        assert "import:uvicorn" in import_names


# ---------------------------------------------------------------------------
# run_doctor — env file check
# ---------------------------------------------------------------------------


class TestRunDoctorEnvFile:
    def test_env_file_ok_when_file_exists(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765\n")
        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")
        item = next(i for i in report.items if i.name == "env_file")
        assert item.status == CheckStatus.OK

    def test_env_file_read_error_is_reported_as_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = tmp_path / ".env"
        env.write_text("MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765\n")
        original_read_text = Path.read_text

        def _read_text(path: Path, *args: object, **kwargs: object) -> str:
            if path == env:
                raise PermissionError("permission denied")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _read_text)

        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")

        item = next(i for i in report.items if i.name == "env_file")
        assert item.status == CheckStatus.FAIL
        assert "cannot be read" in item.message
        assert report.exit_code == 1

    def test_env_file_warn_when_file_absent(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "env_file")
        assert item.status == CheckStatus.WARN

    def test_env_file_warn_message_suggests_setup(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "env_file")
        assert "setup" in item.message.lower()

    def test_env_file_defaults_to_dot_env_in_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        env = tmp_path / ".env"
        env.write_text("MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765\n")
        # Call without env_file argument — should auto-detect .env in CWD.
        report = run_doctor(modal_config_path=tmp_path / "absent.toml")
        item = next(i for i in report.items if i.name == "env_file")
        assert item.status == CheckStatus.OK


# ---------------------------------------------------------------------------
# run_doctor — signing key check
# ---------------------------------------------------------------------------


class TestRunDoctorSigningKey:
    def test_signing_key_ok_via_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", "k1:" + "ab" * 32)
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "signing_key")
        assert item.status == CheckStatus.OK

    def test_signing_key_ok_via_key_file_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key_file = tmp_path / "key.txt"
        key_file.write_text("k1:" + "ab" * 32, encoding="utf-8")
        monkeypatch.setenv("MODAL_MCP_SIGNING_KEY_FILE", str(key_file))
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "signing_key")
        assert item.status == CheckStatus.OK

    def test_signing_key_ok_via_env_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key.txt"
        key_file.write_text("k1:" + "ab" * 32, encoding="utf-8")
        env = tmp_path / ".env"
        _write_env(env, f"MODAL_MCP_SIGNING_KEY_FILE={key_file}\n")
        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")
        item = next(i for i in report.items if i.name == "signing_key")
        assert item.status == CheckStatus.OK

    def test_signing_key_fail_when_key_file_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_SIGNING_KEY_FILE", str(tmp_path / "absent.txt"))
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "signing_key")
        assert item.status == CheckStatus.FAIL

    def test_signing_key_warn_when_not_configured(self, tmp_path: Path) -> None:
        """Absent signing key is a warning, not a failure.

        User may not have run setup yet.
        """
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "signing_key")
        assert item.status == CheckStatus.WARN

    def test_signing_key_not_configured_is_non_fatal(self, tmp_path: Path) -> None:
        """Missing signing key must not produce exit code 1."""
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "signing_key")
        assert item.status != CheckStatus.FAIL

    def test_signing_key_missing_file_is_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicitly configured but absent key file is a hard failure."""
        monkeypatch.setenv("MODAL_MCP_SIGNING_KEY_FILE", str(tmp_path / "absent.txt"))
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "signing_key")
        assert item.status == CheckStatus.FAIL

    def test_signing_key_warn_message_mentions_env_var_names(
        self, tmp_path: Path
    ) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "signing_key")
        assert (
            "MODAL_MCP_SIGNING_KEYS" in item.message
            or "MODAL_MCP_SIGNING_KEY_FILE" in item.message
        )


# ---------------------------------------------------------------------------
# run_doctor — allowed origins check
# ---------------------------------------------------------------------------


class TestRunDoctorAllowedOrigins:
    def test_allowed_origins_ok_via_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_ALLOWED_ORIGINS", "http://127.0.0.1:8765")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "allowed_origins")
        assert item.status == CheckStatus.OK

    def test_allowed_origins_ok_via_env_file(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765\n")
        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")
        item = next(i for i in report.items if i.name == "allowed_origins")
        assert item.status == CheckStatus.OK

    def test_allowed_origins_warn_when_not_set(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "allowed_origins")
        assert item.status == CheckStatus.WARN


# ---------------------------------------------------------------------------
# run_doctor — read-only readiness check
# ---------------------------------------------------------------------------


class TestRunDoctorReadOnly:
    def test_read_only_ok_when_not_set(self, tmp_path: Path) -> None:
        """Absent MODAL_MCP_READ_ONLY defaults to read-only mode (OK)."""
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "read_only")
        assert item.status == CheckStatus.OK

    def test_read_only_ok_when_set_to_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_READ_ONLY", "true")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "read_only")
        assert item.status == CheckStatus.OK

    def test_read_only_ok_when_set_to_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_READ_ONLY", "1")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "read_only")
        assert item.status == CheckStatus.OK

    def test_read_only_warn_when_set_to_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_READ_ONLY", "false")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "read_only")
        assert item.status == CheckStatus.WARN

    def test_read_only_warn_when_set_to_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_READ_ONLY", "0")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "read_only")
        assert item.status == CheckStatus.WARN

    def test_read_only_warn_via_env_file_false_with_export_prefix(
        self, tmp_path: Path
    ) -> None:
        """.env with 'export MODAL_MCP_READ_ONLY=false' must emit a non-OK item."""
        env = tmp_path / ".env"
        _write_env(env, "export MODAL_MCP_READ_ONLY=false\n")
        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")
        item = next(i for i in report.items if i.name == "read_only")
        assert item.status != CheckStatus.OK

    def test_read_only_warn_via_env_file_false_plain(self, tmp_path: Path) -> None:
        """.env with 'MODAL_MCP_READ_ONLY=false' must emit a non-OK item."""
        env = tmp_path / ".env"
        _write_env(env, "MODAL_MCP_READ_ONLY=false\n")
        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")
        item = next(i for i in report.items if i.name == "read_only")
        assert item.status != CheckStatus.OK

    def test_read_only_ok_via_env_file_true(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        _write_env(env, "MODAL_MCP_READ_ONLY=true\n")
        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")
        item = next(i for i in report.items if i.name == "read_only")
        assert item.status == CheckStatus.OK

    def test_read_only_warn_message_mentions_write_operations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_READ_ONLY", "false")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "read_only")
        assert "write" in item.message.lower() or "disabled" in item.message.lower()


# ---------------------------------------------------------------------------
# run_doctor — enabled toolsets check
# ---------------------------------------------------------------------------


class TestRunDoctorToolsets:
    def test_toolsets_ok_when_not_set(self, tmp_path: Path) -> None:
        """Absent MODAL_MCP_ENABLED_TOOLSETS defaults to read-only toolsets (OK)."""
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "toolsets")
        assert item.status == CheckStatus.OK

    def test_toolsets_ok_for_default_read_only_toolsets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_ENABLED_TOOLSETS", "discovery,apps,logs")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "toolsets")
        assert item.status == CheckStatus.OK

    def test_toolsets_warn_when_change_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_ENABLED_TOOLSETS", "discovery,change")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "toolsets")
        assert item.status == CheckStatus.WARN

    def test_toolsets_warn_when_expert_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_ENABLED_TOOLSETS", "discovery,expert")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "toolsets")
        assert item.status == CheckStatus.WARN

    def test_toolsets_warn_when_both_change_and_expert_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_ENABLED_TOOLSETS", "change,expert,discovery")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "toolsets")
        assert item.status == CheckStatus.WARN

    def test_toolsets_warn_via_env_file_change(self, tmp_path: Path) -> None:
        """.env with change toolset must emit a warning."""
        env = tmp_path / ".env"
        _write_env(env, "MODAL_MCP_ENABLED_TOOLSETS=discovery,change\n")
        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")
        item = next(i for i in report.items if i.name == "toolsets")
        assert item.status == CheckStatus.WARN

    def test_toolsets_warn_via_env_file_expert_with_export_prefix(
        self, tmp_path: Path
    ) -> None:
        """.env with 'export MODAL_MCP_ENABLED_TOOLSETS=...,expert,...' must warn."""
        env = tmp_path / ".env"
        _write_env(env, "export MODAL_MCP_ENABLED_TOOLSETS=apps,expert\n")
        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")
        item = next(i for i in report.items if i.name == "toolsets")
        assert item.status == CheckStatus.WARN

    def test_toolsets_warn_message_mentions_read_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_MCP_ENABLED_TOOLSETS", "change")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "toolsets")
        assert (
            "read_only" in item.message.lower()
            or "write" in item.message.lower()
            or "mutating" in item.message.lower()
        )


# ---------------------------------------------------------------------------
# run_doctor — credential check
# ---------------------------------------------------------------------------


class TestRunDoctorCredentials:
    def test_credentials_ok_when_found_in_environ(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "tsec")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "credentials")
        assert item.status == CheckStatus.OK

    def test_credentials_ok_when_found_via_toml(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "modal.toml"
        toml_path.write_text("[token]\nid = 'x'\nsecret = 'y'\n", encoding="utf-8")
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=toml_path
        )
        item = next(i for i in report.items if i.name == "credentials")
        assert item.status == CheckStatus.OK

    def test_credentials_warn_when_none_found(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "credentials")
        assert item.status == CheckStatus.WARN

    def test_credentials_warn_is_non_fatal(self, tmp_path: Path) -> None:
        """Missing credentials must produce a warning, not a failure."""
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "credentials")
        assert item.status != CheckStatus.FAIL


# ---------------------------------------------------------------------------
# run_doctor — SDK auth check
# ---------------------------------------------------------------------------


class TestRunDoctorSdkAuth:
    def test_sdk_auth_uses_selected_env_file_direct_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        modal_config = pytest.importorskip("modal.config")
        env = tmp_path / ".env"
        _write_env(
            env,
            "MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765\n"
            "MODAL_MCP_SIGNING_KEYS=k1:"
            "abababababababababababababababababababababababababababababababab\n"
            "MODAL_TOKEN_ID=tid-from-env-file\n"
            "MODAL_TOKEN_SECRET=tsec-from-env-file\n",
        )

        class FakeConfig:
            def __getitem__(self, key: str) -> str | None:
                if key == "token_id":
                    return os.environ.get("MODAL_TOKEN_ID")
                if key == "token_secret":
                    return os.environ.get("MODAL_TOKEN_SECRET")
                raise KeyError(key)

        monkeypatch.setattr(modal_config, "Config", FakeConfig)

        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")

        item = next(i for i in report.items if i.name == "sdk_auth")
        assert item.status == CheckStatus.OK
        assert os.environ.get("MODAL_TOKEN_ID") is None
        assert os.environ.get("MODAL_TOKEN_SECRET") is None

    def test_sdk_auth_uses_selected_env_file_file_backed_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        modal_config = pytest.importorskip("modal.config")
        id_file, secret_file = _make_token_files(tmp_path)
        env = tmp_path / ".env"
        _write_env(
            env,
            "MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765\n"
            "MODAL_MCP_SIGNING_KEYS=k1:"
            "abababababababababababababababababababababababababababababababab\n"
            f"MODAL_TOKEN_ID_FILE={id_file}\n"
            f"MODAL_TOKEN_SECRET_FILE={secret_file}\n",
        )

        class FakeConfig:
            def __getitem__(self, key: str) -> str | None:
                if key == "token_id":
                    return os.environ.get("MODAL_TOKEN_ID")
                if key == "token_secret":
                    return os.environ.get("MODAL_TOKEN_SECRET")
                raise KeyError(key)

        monkeypatch.setattr(modal_config, "Config", FakeConfig)

        report = run_doctor(env_file=env, modal_config_path=tmp_path / "absent.toml")

        item = next(i for i in report.items if i.name == "sdk_auth")
        assert item.status == CheckStatus.OK
        assert os.environ.get("MODAL_TOKEN_ID") is None
        assert os.environ.get("MODAL_TOKEN_SECRET") is None

    def test_sdk_auth_ok_when_credentials_present_and_probe_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Credentials present + successful probe → sdk_auth OK."""
        pytest.importorskip("modal")
        monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "tsec")
        # Ensure the probe succeeds regardless of real SDK state.
        monkeypatch.setattr(doctor_module, "_probe_modal_auth", lambda *_, **__: None)
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "sdk_auth")
        assert item.status == CheckStatus.OK

    def test_sdk_auth_fail_when_probe_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Credentials present but probe raises → sdk_auth FAIL."""
        pytest.importorskip("modal")
        monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "tsec")

        def _failing_probe(*_: object, **__: object) -> None:
            raise RuntimeError("auth rejected")

        monkeypatch.setattr(doctor_module, "_probe_modal_auth", _failing_probe)
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "sdk_auth")
        assert item.status == CheckStatus.FAIL

    def test_sdk_auth_fail_message_contains_probe_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The FAIL message for a probe error should describe the error."""
        pytest.importorskip("modal")
        monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "tsec")

        def _failing_probe(*_: object, **__: object) -> None:
            raise RuntimeError("connection timeout")

        monkeypatch.setattr(doctor_module, "_probe_modal_auth", _failing_probe)
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "sdk_auth")
        assert "connection timeout" in item.message

    def test_sdk_auth_warn_when_credentials_absent(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "sdk_auth")
        assert item.status == CheckStatus.WARN

    def test_sdk_auth_skipped_message_when_no_credentials(self, tmp_path: Path) -> None:
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "sdk_auth")
        assert (
            "skipped" in item.message.lower()
            or "no credentials" in item.message.lower()
        )

    def test_sdk_auth_fail_when_modal_import_fails_with_credentials(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When modal import fails with credentials, sdk_auth must be FAIL (not OK)."""
        monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "tsec")
        monkeypatch.setattr(
            doctor_module,
            "_import_modal",
            _missing_import("No module named 'modal'"),
        )
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        sdk_auth_items = [i for i in report.items if i.name == "sdk_auth"]
        assert not any(i.status == CheckStatus.OK for i in sdk_auth_items)

    def test_sdk_auth_not_present_when_modal_import_fails_with_credentials(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When modal import fails, no sdk_auth OK item should be added."""
        monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "tsec")
        monkeypatch.setattr(
            doctor_module,
            "_import_modal",
            _missing_import("No module named 'modal'"),
        )
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        # sdk_auth item must not have OK status in this case
        sdk_auth_items = [i for i in report.items if i.name == "sdk_auth"]
        assert not any(i.status == CheckStatus.OK for i in sdk_auth_items)


# ---------------------------------------------------------------------------
# run_doctor — Modal CLI check
# ---------------------------------------------------------------------------


class TestRunDoctorModalCli:
    def test_modal_cli_ok_when_found_in_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        # Create a fake 'modal' executable on PATH.
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        fake_modal = fake_bin / "modal"
        fake_modal.write_text("#!/bin/sh\necho modal\n")
        fake_modal.chmod(0o755)

        original_which = shutil.which

        def patched_which(name: str, *a: object, **kw: object) -> str | None:
            if name == "modal":
                return str(fake_modal)
            return original_which(name, *a, **kw)

        monkeypatch.setattr(shutil, "which", patched_which)

        # Also patch the shutil used inside doctor module
        import modal_mcp.doctor as _doctor

        monkeypatch.setattr(_doctor.shutil, "which", patched_which)

        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "modal_cli")
        assert item.status == CheckStatus.OK

    def test_modal_cli_warn_when_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        import modal_mcp.doctor as _doctor

        original_which = shutil.which

        def no_modal_which(name: str, *a: object, **kw: object) -> str | None:
            if name == "modal":
                return None
            return original_which(name, *a, **kw)

        monkeypatch.setattr(_doctor.shutil, "which", no_modal_which)
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        item = next(i for i in report.items if i.name == "modal_cli")
        assert item.status == CheckStatus.WARN

    def test_modal_cli_is_separate_from_sdk_check(self, tmp_path: Path) -> None:
        """modal_cli and import:modal must be distinct diagnostic items."""
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        names = {i.name for i in report.items}
        assert "modal_cli" in names
        assert "import:modal" in names
        assert "modal_cli" != "import:modal"


# ---------------------------------------------------------------------------
# run_doctor — partial-ready state (acceptance criteria)
# ---------------------------------------------------------------------------


class TestPartialReadyAfterSetup:
    """After 'setup --yes', doctor must return the partial-ready warning state.

    The state is characterised by:
    - env_file: OK  (setup wrote .env)
    - signing_key: OK  (setup wrote key file; .env references it)
    - allowed_origins: OK  (setup wrote MODAL_MCP_ALLOWED_ORIGINS)
    - credentials: WARN  (setup never writes credentials)
    - exit_code: 3  (warnings produce the partial-ready state)
    """

    def test_partial_ready_after_setup_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        from modal_mcp.__main__ import main

        # 1. Run setup --yes in the temp dir.
        assert main(["setup", "--yes"]) == 0

        # 2. Run doctor (env_file defaults to CWD/.env).
        report = run_doctor(modal_config_path=tmp_path / "absent.toml")

        assert report.exit_code == 3, (
            "partial-ready state (warnings, no failures) must have exit code 3"
        )

    def test_partial_ready_env_file_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        from modal_mcp.__main__ import main

        main(["setup", "--yes"])
        report = run_doctor(modal_config_path=tmp_path / "absent.toml")

        item = next(i for i in report.items if i.name == "env_file")
        assert item.status == CheckStatus.OK

    def test_partial_ready_signing_key_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        from modal_mcp.__main__ import main

        main(["setup", "--yes"])
        report = run_doctor(modal_config_path=tmp_path / "absent.toml")

        item = next(i for i in report.items if i.name == "signing_key")
        assert item.status == CheckStatus.OK

    def test_partial_ready_allowed_origins_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        from modal_mcp.__main__ import main

        main(["setup", "--yes"])
        report = run_doctor(modal_config_path=tmp_path / "absent.toml")

        item = next(i for i in report.items if i.name == "allowed_origins")
        assert item.status == CheckStatus.OK

    def test_partial_ready_credentials_warn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        from modal_mcp.__main__ import main

        main(["setup", "--yes"])
        report = run_doctor(modal_config_path=tmp_path / "absent.toml")

        item = next(i for i in report.items if i.name == "credentials")
        assert item.status == CheckStatus.WARN, (
            "setup never writes credentials;"
            " credentials must be WARN in partial-ready state"
        )

    def test_partial_ready_has_warnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        from modal_mcp.__main__ import main

        main(["setup", "--yes"])
        report = run_doctor(modal_config_path=tmp_path / "absent.toml")

        assert report.has_warnings, "partial-ready state must have at least one warning"

    def test_partial_ready_has_no_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        from modal_mcp.__main__ import main

        main(["setup", "--yes"])
        report = run_doctor(modal_config_path=tmp_path / "absent.toml")

        assert not report.has_failures, "partial-ready state must not have failures"


# ---------------------------------------------------------------------------
# run_doctor — works without valid Settings
# ---------------------------------------------------------------------------


class TestDoctorWorksWithoutSettings:
    def test_doctor_does_not_import_settings(self) -> None:
        """doctor.py must not import Settings — validate at import time."""
        import ast
        import inspect

        import modal_mcp.doctor as _doctor

        source = inspect.getsource(_doctor)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and "config" in node.module
            ):
                imported_names = [alias.name for alias in node.names]
                assert "Settings" not in imported_names, (
                    "doctor.py must not import Settings from config"
                )

    def test_doctor_runs_without_any_env_vars(self, tmp_path: Path) -> None:
        """run_doctor must complete even when no env-vars or files are present."""
        report = run_doctor(
            env_file=tmp_path / "absent.env",
            modal_config_path=tmp_path / "absent.toml",
        )
        # Should complete without raising and should return a report
        assert isinstance(report, DiagnosticReport)

    def test_doctor_exit_code_reflects_failures(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            doctor_module,
            "_import_fastmcp",
            _missing_import("No module named 'fastmcp'"),
        )
        report = run_doctor(
            env_file=tmp_path / "absent.env", modal_config_path=tmp_path / "absent.toml"
        )
        assert report.exit_code == 1


# ---------------------------------------------------------------------------
# CLI integration (via __main__)
# ---------------------------------------------------------------------------


class TestDoctorCli:
    def test_doctor_returns_zero_when_all_deps_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main(['doctor']) returns 0 or 3 when all packages are importable.

        DiagnosticReport.exit_code is now surfaced directly by the CLI:
        0 = all OK, 3 = warnings-only (partial-ready), 1 = hard failures.
        In a clean tmp_path with no .env or signing key configured, the
        unconfigured items produce WARN items (not FAIL), so the exit code
        is 3 (partial-ready) — never 0 (which requires zero warnings) and
        never 1 (which requires at least one hard failure).
        """
        # Run from a clean directory with no .env or signing key, so we get
        # warnings (not failures) for unconfigured items.
        monkeypatch.chdir(tmp_path)
        from modal_mcp.__main__ import main

        result = main(["doctor"])
        assert result in {0, 3}, (
            f"doctor must not report FAIL-level failures in a clean environment; "
            f"got exit code {result}"
        )

    def test_doctor_returns_one_when_import_fails(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            doctor_module,
            "_import_fastmcp",
            _missing_import("No module named 'fastmcp'"),
        )
        from modal_mcp.__main__ import main

        result = main(["doctor"])
        assert result == 1

    def test_doctor_fail_written_to_stderr(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            doctor_module,
            "_import_fastmcp",
            _missing_import("No module named 'fastmcp'"),
        )
        from modal_mcp.__main__ import main

        main(["doctor"])
        captured = capsys.readouterr()
        assert "fastmcp" in captured.err

    def test_doctor_partial_ready_message_when_warnings_no_failures(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        from modal_mcp.__main__ import main

        # Run doctor in a clean directory (no .env, no signing key).
        # Package imports succeed, but env/signing-key/origins are missing,
        # producing warnings without failures -> exit code 3 (partial-ready).
        result = main(["doctor"])
        assert result == 3
        out = capsys.readouterr().out
        assert "partial ready" in out.lower()


# ---------------------------------------------------------------------------
# Redaction coverage
# ---------------------------------------------------------------------------


class TestDoctorRedaction:
    """run_doctor and the doctor CLI must never expose sensitive values."""

    # ------------------------------------------------------------------
    # run_doctor() message-level tests (mock-injection probes)
    # ------------------------------------------------------------------

    def test_run_doctor_redacts_token_secret_injected_into_credential_detail(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Token secret injected into a credential detail must be stripped."""
        secret_value = "tok_secret_injected_into_detail_xyz"
        monkeypatch.setenv("MODAL_TOKEN_SECRET", secret_value)

        # Patch probe_credentials to leak the secret in the detail field,
        # simulating an accidental exposure path.
        monkeypatch.setattr(
            doctor_module,
            "probe_credentials",
            lambda *_a, **_kw: doctor_module.CredentialProbeResult(
                found=True,
                source="environ",
                detail=f"token secret: {secret_value}",
            ),
        )

        report = run_doctor(
            env_file=tmp_path / "absent.env",
            modal_config_path=tmp_path / "absent.toml",
        )

        for item in report.items:
            assert secret_value not in item.message, (
                f"Token secret leaked in {item.name!r}: {item.message!r}"
            )

    def test_run_doctor_redacts_token_id_injected_into_credential_detail(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Token ID injected into a credential detail must be stripped."""
        token_id = "tok_id_injected_into_detail_abc123"
        monkeypatch.setenv("MODAL_TOKEN_ID", token_id)

        monkeypatch.setattr(
            doctor_module,
            "probe_credentials",
            lambda *_a, **_kw: doctor_module.CredentialProbeResult(
                found=True,
                source="environ",
                detail=f"token_id={token_id}",
            ),
        )

        report = run_doctor(
            env_file=tmp_path / "absent.env",
            modal_config_path=tmp_path / "absent.toml",
        )

        for item in report.items:
            assert token_id not in item.message, (
                f"Token ID leaked in {item.name!r}: {item.message!r}"
            )

    def test_run_doctor_redacts_signing_key_injected_into_signing_key_message(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Signing key hex injected into a diagnostic message must be stripped."""
        hex_key = "ab" * 32  # 64-char hex material
        full_key = f"k1:{hex_key}"
        monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", full_key)

        # Patch _check_signing_key to inject the hex key directly into the
        # message, simulating an accidental disclosure path.
        monkeypatch.setattr(
            doctor_module,
            "_check_signing_key",
            lambda _env: doctor_module.DiagnosticItem(
                "signing_key",
                doctor_module.CheckStatus.OK,
                f"key material: {hex_key}",
            ),
        )

        report = run_doctor(
            env_file=tmp_path / "absent.env",
            modal_config_path=tmp_path / "absent.toml",
        )

        sk_item = next(i for i in report.items if i.name == "signing_key")
        assert hex_key not in sk_item.message, (
            f"Signing key hex leaked in signing_key message: {sk_item.message!r}"
        )

    def test_run_doctor_redacts_full_signing_key_string(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The full kid:hex string injected into a message must be stripped."""
        hex_key = "cd" * 32
        full_key = f"k2:{hex_key}"
        monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", full_key)

        monkeypatch.setattr(
            doctor_module,
            "_check_signing_key",
            lambda _env: doctor_module.DiagnosticItem(
                "signing_key",
                doctor_module.CheckStatus.FAIL,
                f"key={full_key}",
            ),
        )

        report = run_doctor(
            env_file=tmp_path / "absent.env",
            modal_config_path=tmp_path / "absent.toml",
        )

        sk_item = next(i for i in report.items if i.name == "signing_key")
        assert full_key not in sk_item.message
        assert hex_key not in sk_item.message

    def test_run_doctor_leaves_non_secret_messages_intact(
        self,
        tmp_path: Path,
    ) -> None:
        """Non-sensitive content in diagnostic messages must not be altered."""
        report = run_doctor(
            env_file=tmp_path / "absent.env",
            modal_config_path=tmp_path / "absent.toml",
        )
        # The env_file warning should still mention the file path.
        env_item = next(i for i in report.items if i.name == "env_file")
        assert "env" in env_item.message.lower()

    # ------------------------------------------------------------------
    # CLI output — stdout / stderr must not contain secrets
    # ------------------------------------------------------------------

    def test_doctor_cli_stdout_does_not_contain_token_secret(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Token secret injected into a diagnostic message must not appear in stdout."""
        from modal_mcp.__main__ import main

        secret = "cli_tok_secret_must_not_appear_in_stdout_xyz"
        monkeypatch.setenv("MODAL_TOKEN_SECRET", secret)
        monkeypatch.chdir(tmp_path)

        monkeypatch.setattr(
            doctor_module,
            "probe_credentials",
            lambda *_a, **_kw: doctor_module.CredentialProbeResult(
                found=True,
                source="environ",
                detail=f"secret={secret}",
            ),
        )

        main(["doctor"])
        captured = capsys.readouterr()
        assert secret not in captured.out
        assert secret not in captured.err

    def test_doctor_cli_stderr_does_not_contain_signing_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Signing key injected into a FAIL message must not appear in stderr."""
        from modal_mcp.__main__ import main

        hex_key = "ef" * 32
        full_key = f"k3:{hex_key}"
        monkeypatch.setenv("MODAL_MCP_SIGNING_KEYS", full_key)
        monkeypatch.chdir(tmp_path)

        monkeypatch.setattr(
            doctor_module,
            "_check_signing_key",
            lambda _env: doctor_module.DiagnosticItem(
                "signing_key",
                doctor_module.CheckStatus.FAIL,
                f"broken key: {hex_key}",
            ),
        )

        main(["doctor"])
        captured = capsys.readouterr()
        assert hex_key not in captured.out
        assert hex_key not in captured.err

    def test_doctor_cli_stdout_does_not_contain_token_id_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Token ID from the environment must never reach doctor stdout/stderr."""
        from modal_mcp.__main__ import main

        token_id = "tok_id_must_not_appear_in_doctor_cli_output"
        monkeypatch.setenv("MODAL_TOKEN_ID", token_id)
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "tsec-placeholder")
        monkeypatch.chdir(tmp_path)

        monkeypatch.setattr(
            doctor_module,
            "probe_credentials",
            lambda *_a, **_kw: doctor_module.CredentialProbeResult(
                found=True,
                source="environ",
                detail=f"id={token_id}",
            ),
        )

        main(["doctor"])
        captured = capsys.readouterr()
        assert token_id not in captured.out
        assert token_id not in captured.err

    def test_sdk_auth_probe_failure_does_not_leak_token_secret(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Credentials present but probe fails: credential values must be redacted."""
        secret_value = "probe_fail_secret_must_not_appear_abcxyz"
        monkeypatch.setenv("MODAL_TOKEN_ID", "tid")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", secret_value)

        # Probe raises with the secret value embedded in the error message,
        # simulating an accidental exposure path.
        def _leaky_probe(*_: object, **__: object) -> None:
            raise RuntimeError(f"auth error: token={secret_value}")

        monkeypatch.setattr(doctor_module, "_probe_modal_auth", _leaky_probe)

        report = run_doctor(
            env_file=tmp_path / "absent.env",
            modal_config_path=tmp_path / "absent.toml",
        )

        sdk_item = next(i for i in report.items if i.name == "sdk_auth")
        assert sdk_item.status == CheckStatus.FAIL
        for item in report.items:
            assert secret_value not in item.message, (
                f"Token secret leaked in {item.name!r}: {item.message!r}"
            )

    def test_file_backed_token_secret_content_is_redacted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Contents of file-backed token secret files must be redacted from messages."""
        id_file, secret_file = _make_token_files(tmp_path)
        # Overwrite secret file with a recognisable value.
        secret_content = "file_secret_must_not_appear_in_messages_xyz"
        secret_file.write_text(secret_content, encoding="utf-8")

        monkeypatch.setenv("MODAL_TOKEN_ID_FILE", str(id_file))
        monkeypatch.setenv("MODAL_TOKEN_SECRET_FILE", str(secret_file))

        # Inject the file secret into a credential detail to simulate leakage.
        monkeypatch.setattr(
            doctor_module,
            "probe_credentials",
            lambda *_a, **_kw: doctor_module.CredentialProbeResult(
                found=True,
                source="file_backed",
                detail=f"secret file content: {secret_content}",
            ),
        )

        report = run_doctor(
            env_file=tmp_path / "absent.env",
            modal_config_path=tmp_path / "absent.toml",
        )

        for item in report.items:
            assert secret_content not in item.message, (
                f"File-backed token secret leaked in {item.name!r}: {item.message!r}"
            )
