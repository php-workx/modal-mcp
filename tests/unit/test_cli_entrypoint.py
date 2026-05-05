"""Unit tests for the modal-mcp CLI entrypoint."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

import modal_mcp.doctor as doctor_module
from modal_mcp.__main__ import build_parser, main

# ---------------------------------------------------------------------------
# Backward-compatibility: no subcommand still starts the server
# ---------------------------------------------------------------------------


def test_main_delegates_to_server_run() -> None:
    """main([]) must invoke modal_mcp.server.run() exactly once."""
    with patch("modal_mcp.server.run") as mock_run:
        result = main([])
    mock_run.assert_called_once_with()
    assert result == 0


# ---------------------------------------------------------------------------
# pyproject.toml console script
# ---------------------------------------------------------------------------


def test_pyproject_console_script_points_to_cli_main() -> None:
    """pyproject.toml console script must remain modal_mcp.__main__:main."""
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    scripts = data["project"]["scripts"]
    assert scripts.get("modal-mcp") == "modal_mcp.__main__:main"


# ---------------------------------------------------------------------------
# Parser structure: subcommands must be registered
# ---------------------------------------------------------------------------


def test_parser_has_subparsers() -> None:
    """build_parser() must register subparsers (verified via parse behaviour)."""
    parser = build_parser()
    # If add_subparsers was called, known subcommands parse cleanly and the
    # 'subcommand' dest is populated.
    args = parser.parse_args(["run"])
    assert args.subcommand == "run"


@pytest.mark.parametrize(
    "subcommand",
    ["run", "setup", "doctor", "print-agent-config"],
)
def test_subcommand_registered(subcommand: str) -> None:
    """Each expected subcommand must be parseable by the parser."""
    parser = build_parser()
    # Parsing --help for the subcommand exits 0; any other exception means the
    # subcommand is not registered.
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([subcommand, "--help"])
    assert exc_info.value.code == 0, (
        f"Subcommand '{subcommand}' exited with non-zero code from --help"
    )


def test_subcommands_visible_in_help(capsys: pytest.CaptureFixture[str]) -> None:
    """All four subcommands must appear in the --help output."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    captured = capsys.readouterr()
    for subcommand in ("run", "setup", "doctor", "print-agent-config"):
        assert subcommand in captured.out, f"'{subcommand}' not found in --help output"


# ---------------------------------------------------------------------------
# 'run' subcommand
# ---------------------------------------------------------------------------


def test_run_subcommand_delegates_to_server_run() -> None:
    """main(['run']) must invoke modal_mcp.server.run() exactly once."""
    with patch("modal_mcp.server.run") as mock_run:
        result = main(["run"])
    mock_run.assert_called_once_with()
    assert result == 0


def test_run_subcommand_accepts_env_file_flag() -> None:
    """main(['run', '--env-file', '/tmp/x.env']) must not raise a parse error."""
    with patch("modal_mcp.server.run"):
        result = main(["run", "--env-file", "/tmp/x.env"])
    assert result == 0


def test_run_env_file_populates_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--env-file vars must be visible in os.environ when server.run() is called."""
    env_file = tmp_path / "custom.env"
    env_file.write_text("MODAL_MCP_TEST_SENTINEL=xyz789\n", encoding="utf-8")
    monkeypatch.delenv("MODAL_MCP_TEST_SENTINEL", raising=False)

    seen_in_run: list[str | None] = []

    def capturing_run() -> None:
        seen_in_run.append(os.environ.get("MODAL_MCP_TEST_SENTINEL"))

    with patch("modal_mcp.server.run", side_effect=capturing_run):
        result = main(["run", "--env-file", str(env_file)])

    # Clean up: dotenv.load_dotenv modifies os.environ directly and is not
    # tracked by monkeypatch, so we remove the sentinel key explicitly.
    os.environ.pop("MODAL_MCP_TEST_SENTINEL", None)

    assert result == 0
    assert seen_in_run == ["xyz789"], (
        "--env-file must populate os.environ before server.run() is called"
    )


# ---------------------------------------------------------------------------
# 'setup' subcommand
# ---------------------------------------------------------------------------


def test_setup_subcommand_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """main(['setup']) must return 0 and emit non-empty output."""
    result = main(["setup"])
    assert result == 0
    captured = capsys.readouterr()
    assert captured.out.strip() != ""


# ---------------------------------------------------------------------------
# 'doctor' subcommand
# ---------------------------------------------------------------------------


def test_doctor_subcommand_returns_zero_when_deps_present() -> None:
    """main(['doctor']) returns 0 or 3 when all required packages are importable.

    Exit code is now DiagnosticReport.exit_code: 0 = all OK, 3 = warnings,
    1 = failures.  In a typical dev environment, missing .env or signing-key
    produce WARN (not FAIL), so exit code is 0 or 3 — never 1.
    """
    result = main(["doctor"])
    # All dependencies (modal_mcp, modal, fastmcp, uvicorn) are installed in the
    # venv; doctor must not report any FAIL-level failures.
    assert result != 1, (
        f"doctor must not report FAIL-level failures; got exit code {result}"
    )
    assert result in {0, 3}, f"doctor exit code must be 0 or 3; got {result}"


def test_doctor_subcommand_fails_when_import_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main(['doctor']) returns 1 and writes to stderr when a required dep is missing.

    ``fastmcp`` is a hard dependency (FAIL-level); its absence must produce
    exit code 1.  Note: ``modal`` SDK absence is intentionally non-fatal
    (WARN-level), so it is not used here.
    """

    def missing_fastmcp() -> None:
        raise ImportError("No module named 'fastmcp'")

    monkeypatch.setattr(doctor_module, "_import_fastmcp", missing_fastmcp)
    result = main(["doctor"])
    assert result == 1
    captured = capsys.readouterr()
    assert "fastmcp" in captured.err


# ---------------------------------------------------------------------------
# 'print-agent-config' subcommand
# ---------------------------------------------------------------------------


def test_print_agent_config_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main(['print-agent-config']) must return 0."""
    result = main(["print-agent-config"])
    assert result == 0


def _extract_json_from_output(output: str) -> dict:
    """Strip leading comment lines and parse the remaining text as JSON.

    The ``print-agent-config`` output includes human-readable comment lines
    (prefixed with ``#``) followed by the JSON snippet.  JSON-level assertions
    must strip those comment lines before calling ``json.loads``.
    """
    json_lines = [ln for ln in output.splitlines() if not ln.startswith("#")]
    return json.loads("\n".join(json_lines).strip())


def test_print_agent_config_outputs_valid_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main(['print-agent-config']) must emit a JSON block (after comment header)."""
    main(["print-agent-config"])
    captured = capsys.readouterr()
    parsed = _extract_json_from_output(captured.out)
    assert isinstance(parsed, dict), "Output must contain a JSON object"


def test_print_agent_config_contains_mcp_servers_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Config JSON must contain the 'mcpServers' top-level key."""
    main(["print-agent-config"])
    captured = capsys.readouterr()
    parsed = _extract_json_from_output(captured.out)
    assert "mcpServers" in parsed


def test_print_agent_config_contains_modal_mcp_entry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Config JSON must contain a 'modal-mcp' entry with type and url fields."""
    main(["print-agent-config"])
    captured = capsys.readouterr()
    parsed = _extract_json_from_output(captured.out)
    entry = parsed.get("mcpServers", {}).get("modal-mcp", {})
    assert "type" in entry
    assert "url" in entry
    assert entry["url"].startswith("http://")


# ---------------------------------------------------------------------------
# 'print-agent-config --target claude' — AC #1 command
# ---------------------------------------------------------------------------


def test_print_agent_config_target_claude_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main(['print-agent-config', '--target', 'claude']) must return 0.

    This is the literal command stated in acceptance criterion #1.
    """
    result = main(["print-agent-config", "--target", "claude"])
    assert result == 0


def test_print_agent_config_target_claude_outputs_complete_snippet(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """'--target claude' must emit a complete config snippet with a JSON block."""
    main(["print-agent-config", "--target", "claude"])
    captured = capsys.readouterr()
    parsed = _extract_json_from_output(captured.out)
    entry = parsed.get("mcpServers", {}).get("modal-mcp", {})
    assert entry.get("type") == "sse"
    assert entry.get("url", "").startswith("http://")


def test_print_agent_config_output_states_http_or_sse_transport(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Output must state whether the transport is HTTP/SSE or stdio.

    AC #2 of the original ticket requires the snippet to 'state whether it
    expects HTTP or command-launched MCP'.  The output must contain 'sse' or
    'http' so users understand a running server is required before Claude
    Desktop connects.
    """
    main(["print-agent-config", "--target", "claude"])
    captured = capsys.readouterr()
    output_lower = captured.out.lower()
    assert "sse" in output_lower or "http" in output_lower, (
        "Output must mention the SSE/HTTP transport so users know a running "
        "server is required"
    )


def test_print_agent_config_output_contains_startup_command_with_absolute_env_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Output must include 'modal-mcp run --env-file <absolute-path>'.

    AC #2 requires 'The Claude snippet uses an absolute --env-file path or
    explicit cwd for command launch.'  The startup command hint must be present
    and must reference an absolute path (starting with '/') so users know how
    to start the server before Claude Desktop connects.
    """
    import re

    main(["print-agent-config", "--target", "claude"])
    captured = capsys.readouterr()
    cmd_lines = [
        ln
        for ln in captured.out.splitlines()
        if "modal-mcp run" in ln and "--env-file" in ln
    ]
    assert cmd_lines, (
        "Output must contain a startup command line with 'modal-mcp run --env-file'"
    )
    for line in cmd_lines:
        match = re.search(r"--env-file\s+(\S+)", line)
        assert match, f"Could not parse --env-file path from: {line!r}"
        env_path = match.group(1).strip("\"'")
        assert Path(env_path).is_absolute(), (
            f"env-file path in startup command must be absolute; got: {env_path!r}"
        )


# ---------------------------------------------------------------------------
# AC #1 — 'setup --yes --env-file <path> --secrets-dir <dir>'
# ---------------------------------------------------------------------------


def test_setup_yes_custom_env_file_and_secrets_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #1: setup --yes writes artifacts at explicitly specified paths."""
    custom_env = tmp_path / "custom.env"
    custom_secrets = tmp_path / "custom-secrets"

    result = main(
        [
            "setup",
            "--yes",
            "--env-file",
            str(custom_env),
            "--secrets-dir",
            str(custom_secrets),
        ]
    )

    assert result == 0, "setup --yes with custom paths must exit 0"
    assert custom_env.exists(), f"Expected env file at {custom_env}"
    assert (custom_secrets / "signing-key.txt").exists(), (
        f"Expected signing key at {custom_secrets / 'signing-key.txt'}"
    )


def test_setup_yes_custom_paths_env_file_appears_in_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """setup --yes with custom paths must echo the actual paths in output."""
    custom_env = tmp_path / "myapp.env"
    custom_secrets = tmp_path / "mysecrets"

    main(
        [
            "setup",
            "--yes",
            "--env-file",
            str(custom_env),
            "--secrets-dir",
            str(custom_secrets),
        ]
    )
    captured = capsys.readouterr()
    assert str(custom_env) in captured.out, "Custom env-file path must appear in output"
    assert str(custom_secrets) in captured.out, (
        "Custom secrets-dir path must appear in output"
    )


# ---------------------------------------------------------------------------
# AC #2 — 'setup --yes --force'
# ---------------------------------------------------------------------------


def test_setup_yes_force_exits_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #2: setup --yes --force exits 0 when replacement succeeds."""
    custom_env = tmp_path / ".env"
    custom_secrets = tmp_path / ".secrets"

    # Create the files once to prove force replaces them.
    main(
        [
            "setup",
            "--yes",
            "--env-file",
            str(custom_env),
            "--secrets-dir",
            str(custom_secrets),
        ]
    )
    capsys.readouterr()  # clear output from first run
    result = main(
        [
            "setup",
            "--yes",
            "--force",
            "--env-file",
            str(custom_env),
            "--secrets-dir",
            str(custom_secrets),
        ]
    )

    assert result == 0, "setup --yes --force must exit 0"
    # Force must overwrite: mtime must be >= original (equal on fast filesystems).
    captured = capsys.readouterr()
    assert "created" in captured.out, (
        "Output must indicate the key was (re-)created when --force is used"
    )


def test_setup_yes_force_without_existing_files_exits_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """setup --yes --force must also succeed when no files exist yet."""
    custom_env = tmp_path / "fresh.env"
    custom_secrets = tmp_path / "fresh-secrets"

    result = main(
        [
            "setup",
            "--yes",
            "--force",
            "--env-file",
            str(custom_env),
            "--secrets-dir",
            str(custom_secrets),
        ]
    )

    assert result == 0
    assert custom_env.exists()
    assert (custom_secrets / "signing-key.txt").exists()


# ---------------------------------------------------------------------------
# AC #3 — 'doctor --env-file <path>'
# ---------------------------------------------------------------------------


def test_doctor_accepts_env_file_flag_missing_file() -> None:
    """AC #3: doctor --env-file accepts a missing path without an argparse error."""
    # A missing file is valid input to doctor; it produces a diagnostic item,
    # not a usage error.  The return value must not be 2 (argparse error code).
    result = main(["doctor", "--env-file", "/tmp/definitely-missing-for-modal-mcp.env"])
    assert result != 2, (
        "doctor --env-file must not return 2 (argparse usage error) for a missing file"
    )


def test_doctor_env_file_flag_accepted_by_parser() -> None:
    """build_parser() must register --env-file for the doctor subcommand."""
    parser = build_parser()
    args = parser.parse_args(["doctor", "--env-file", "/tmp/test.env"])
    assert args.env_file == "/tmp/test.env"
    assert args.subcommand == "doctor"


def test_doctor_env_file_returns_diagnostic_exit_code(
    tmp_path: Path,
) -> None:
    """AC #3: doctor --env-file returns DiagnosticReport.exit_code."""
    # A non-existent env file triggers WARN checks; exit code is 0, 1, or 3.
    result = main(["doctor", "--env-file", str(tmp_path / "nonexistent.env")])
    assert result in {0, 1, 3}, (
        "doctor exit code must be 0, 1, or 3 "
        f"(DiagnosticReport.exit_code); got {result}"
    )


def test_doctor_exits_3_for_warnings_only_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_cmd_doctor must pass report.exit_code through — exit code 3 on warnings-only.

    This is a direct regression guard for the requirement that the CLI does NOT
    normalise exit code 3 to 0.  A warnings-only DiagnosticReport has
    exit_code == 3 and the CLI must return exactly 3.
    """
    from modal_mcp.doctor import CheckStatus, DiagnosticItem, DiagnosticReport

    warnings_report = DiagnosticReport(
        items=[
            DiagnosticItem("env_file", CheckStatus.WARN, "env file not found"),
            DiagnosticItem(
                "signing_key", CheckStatus.WARN, "no signing key configured"
            ),
        ]
    )

    monkeypatch.setattr(doctor_module, "run_doctor", lambda **_kw: warnings_report)
    result = main(["doctor"])
    assert result == warnings_report.exit_code == 3, (
        f"_cmd_doctor must return report.exit_code (3) for warnings-only report;"
        f" got {result}"
    )


def test_doctor_exits_1_for_failure_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_cmd_doctor must pass report.exit_code through — exit code 1 on failures.

    Redundant with the existing fastmcp-missing test but directly verifies
    the passthrough by injecting a known DiagnosticReport rather than
    relying on an import side-effect.
    """
    from modal_mcp.doctor import CheckStatus, DiagnosticItem, DiagnosticReport

    failure_report = DiagnosticReport(
        items=[
            DiagnosticItem(
                "import:fastmcp",
                CheckStatus.FAIL,
                "fastmcp not importable: No module named 'fastmcp'",
            )
        ]
    )

    monkeypatch.setattr(doctor_module, "run_doctor", lambda **_kw: failure_report)
    result = main(["doctor"])
    assert result == failure_report.exit_code == 1, (
        f"_cmd_doctor must return report.exit_code (1) for failure report; got {result}"
    )
    # FAIL items must be written to stderr.
    captured = capsys.readouterr()
    assert "fastmcp" in captured.err


# ---------------------------------------------------------------------------
# AC #4 — 'print-agent-config --target codex --env-file <absolute-path>'
# ---------------------------------------------------------------------------


def test_print_agent_config_codex_env_file_appears_in_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #4: codex config snippet must embed the exact --env-file path."""
    env_file = tmp_path / "test.env"
    env_file_str = str(env_file)

    result = main(
        ["print-agent-config", "--target", "codex", "--env-file", env_file_str]
    )

    assert result == 0
    captured = capsys.readouterr()
    assert env_file_str in captured.out, (
        f"Codex config snippet must contain the exact env-file path {env_file_str!r}"
    )


def test_print_agent_config_codex_env_file_in_args_field(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #4: the env-file path must appear in the --env-file args position."""
    env_file = tmp_path / "example.env"
    env_file_str = str(env_file)

    main(["print-agent-config", "--target", "codex", "--env-file", env_file_str])
    captured = capsys.readouterr()
    # The codex snippet contains: args = ["run", "--env-file", "<path>"]
    assert "--env-file" in captured.out
    assert env_file_str in captured.out


# ---------------------------------------------------------------------------
# AC #5 — 'print-agent-config --target claude --env-file <absolute-path>'
# ---------------------------------------------------------------------------


def test_print_agent_config_claude_env_file_appears_in_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #5: claude config snippet must embed the exact --env-file path."""
    env_file = tmp_path / "custom.env"
    env_file_str = str(env_file)

    result = main(
        ["print-agent-config", "--target", "claude", "--env-file", env_file_str]
    )

    assert result == 0
    captured = capsys.readouterr()
    assert env_file_str in captured.out, (
        f"Claude config snippet must contain the exact env-file path {env_file_str!r}"
    )


def test_print_agent_config_env_file_flag_accepted_by_parser() -> None:
    """build_parser() must register --env-file for the print-agent-config subcommand."""
    parser = build_parser()
    args = parser.parse_args(
        ["print-agent-config", "--target", "codex", "--env-file", "/abs/path/test.env"]
    )
    assert args.env_file == "/abs/path/test.env"
    assert args.subcommand == "print-agent-config"


def test_setup_install_codex_relative_env_file_is_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """setup --install codex --env-file <relative> must fail with exit code 1.

    Codex embeds the env-file path verbatim in its config so the path must
    be absolute.  Silently calling .absolute() on a relative input resolves
    against the CWD of the process that ran the command, which is almost
    certainly wrong when the config is later read by a different process.
    The CLI must reject such input with a clear error before calling
    install_codex_config (which also enforces absoluteness internally, but
    only after the path has been silently made absolute).
    """
    result = main(["setup", "--install", "codex", "--env-file", "relative.env"])
    assert result == 1, "setup --install codex with a relative --env-file must exit 1"
    captured = capsys.readouterr()
    error_text = captured.err.lower()
    assert "absolute" in error_text or "--env-file" in error_text, (
        "Error message must mention 'absolute' or '--env-file' so the user "
        "understands the constraint"
    )


def test_setup_install_codex_absolute_env_file_passes_path_guard(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """setup --install codex --env-file <absolute> must not be blocked by path guard.

    The CLI-level absolute-path check must only reject genuinely relative
    paths.  An absolute path must pass the guard and reach install_codex_config
    (mocked here so the test is hermetic and requires no Codex installation).
    """
    abs_env = tmp_path / "test.env"

    with patch(
        "modal_mcp.agent_config.install_codex_config", return_value="dry_run"
    ) as mock_install:
        result = main(
            ["setup", "--install", "codex", "--env-file", str(abs_env), "--dry-run"]
        )

    # The CLI-level guard must not have rejected the path.
    captured = capsys.readouterr()
    assert result != 1 or "absolute" not in captured.err.lower(), (
        "An absolute --env-file must not be rejected by the CLI relative-path guard"
    )
    # install_codex_config must have been called — the guard did not short-circuit.
    mock_install.assert_called_once()
    call_kwargs = mock_install.call_args
    passed_env = call_kwargs.kwargs.get("env_file") or call_kwargs.args[0]
    assert Path(passed_env).is_absolute(), (
        "install_codex_config must receive an absolute env_file path"
    )


# ---------------------------------------------------------------------------
# AC #6 — 'setup --install claude --dry-run'
# ---------------------------------------------------------------------------


def test_setup_install_claude_dry_run_exits_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #6: setup --install claude --dry-run exits 0 without writing any files."""
    fake_config = tmp_path / "claude_desktop_config.json"

    with patch(
        "modal_mcp.agent_targets.claude.get_claude_config_path",
        return_value=fake_config,
    ):
        result = main(["setup", "--install", "claude", "--dry-run"])

    assert result == 0, "setup --install claude --dry-run must exit 0"
    assert not fake_config.exists(), (
        "dry-run must not write the claude_desktop_config.json file"
    )


def test_setup_install_claude_dry_run_output_contains_config_filename(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #6: dry-run output must include 'claude_desktop_config.json'."""
    fake_config = tmp_path / "claude_desktop_config.json"

    with patch(
        "modal_mcp.agent_targets.claude.get_claude_config_path",
        return_value=fake_config,
    ):
        main(["setup", "--install", "claude", "--dry-run"])

    captured = capsys.readouterr()
    assert "claude_desktop_config.json" in captured.out, (
        "Dry-run output must show the target file name 'claude_desktop_config.json'"
    )


def test_setup_install_claude_dry_run_no_config_modified(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #6: dry-run must not create or modify the Claude Desktop config file."""
    fake_config = tmp_path / "claude_desktop_config.json"
    # Pre-condition: file does not exist.
    assert not fake_config.exists()

    with patch(
        "modal_mcp.agent_targets.claude.get_claude_config_path",
        return_value=fake_config,
    ):
        main(["setup", "--install", "claude", "--dry-run"])

    # Post-condition: file still does not exist.
    assert not fake_config.exists(), (
        "setup --install claude --dry-run must not create the config file"
    )


def test_setup_install_claude_is_accepted_by_parser() -> None:
    """build_parser() must accept 'claude' as a valid --install choice."""
    parser = build_parser()
    args = parser.parse_args(["setup", "--install", "claude", "--dry-run"])
    assert args.install == "claude"
    assert args.dry_run is True
