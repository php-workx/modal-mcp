"""Unit tests for the Claude Desktop agent install contract.

These tests assert the structured contract fields *before* any install code
runs, ensuring that the contract is complete and accurate enough to implement
install without further research.

The tests do **not** write any files or invoke any install logic; they only
inspect the contract specification and its helper functions.
"""

from __future__ import annotations

import dataclasses
import io as _io
import json as _json_module
import sys
from pathlib import Path
from unittest.mock import patch
from unittest.mock import patch as _patch

import pytest

from modal_mcp.agent_config import ClaudeInstallError, install_claude_config
from modal_mcp.agent_targets.claude import (
    CLAUDE_AGENT_NAME,
    CLAUDE_BACKUP_SUFFIX_TEMPLATE,
    CLAUDE_CONFIG_FILENAME,
    CLAUDE_CONTRACT,
    CLAUDE_DEFAULT_BIND,
    CLAUDE_ENV_FILE_FLAG,
    CLAUDE_IDEMPOTENCY_KEY,
    CLAUDE_MCP_SSE_PATH,
    CLAUDE_SERVER_NAME,
    CLAUDE_SSE_URL,
    CLAUDE_TOP_LEVEL_KEY,
    CLAUDE_TRANSPORT,
    AgentTargetContract,
    build_contract,
    format_startup_command,
    get_claude_config_dir,
    get_claude_config_path,
)

# ---------------------------------------------------------------------------
# Contract structure
# ---------------------------------------------------------------------------


def test_claude_contract_is_agent_target_contract() -> None:
    """CLAUDE_CONTRACT must be an instance of AgentTargetContract."""
    assert isinstance(CLAUDE_CONTRACT, AgentTargetContract)


def test_claude_contract_is_frozen() -> None:
    """AgentTargetContract instances must be immutable (frozen dataclass)."""
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        CLAUDE_CONTRACT.agent_name = "tampered"  # type: ignore[misc]


def test_claude_contract_declares_required_fields() -> None:
    """All required contract fields must be populated with non-empty values."""
    c = CLAUDE_CONTRACT

    required_str_fields: list[tuple[str, str]] = [
        ("agent_name", c.agent_name),
        ("config_format", c.config_format),
        ("mcp_transport", c.mcp_transport),
        ("server_name", c.server_name),
        ("top_level_key", c.top_level_key),
        ("backup_suffix_template", c.backup_suffix_template),
        ("parse_validation_strategy", c.parse_validation_strategy),
        ("dry_run_description", c.dry_run_description),
        ("idempotency_key", c.idempotency_key),
        ("env_file_strategy", c.env_file_strategy),
    ]
    for field_name, value in required_str_fields:
        assert value, f"contract field '{field_name}' must not be empty"

    # representative_config_path must be a non-trivial Path
    assert c.representative_config_path != Path()
    assert str(c.representative_config_path)

    # refusal_conditions must have at least three entries
    assert len(c.refusal_conditions) >= 3, (
        "at least three refusal conditions must be declared"
    )
    assert all(cond for cond in c.refusal_conditions), (
        "each refusal condition must be a non-empty string"
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_claude_contract_agent_name() -> None:
    """agent_name must equal the stable constant."""
    assert CLAUDE_CONTRACT.agent_name == CLAUDE_AGENT_NAME
    assert CLAUDE_CONTRACT.agent_name == "claude_desktop"


def test_claude_contract_agent_name_contains_claude() -> None:
    """agent_name must identify this as a Claude product target."""
    assert "claude" in CLAUDE_CONTRACT.agent_name.lower()


# ---------------------------------------------------------------------------
# Config location
# ---------------------------------------------------------------------------


def test_claude_contract_config_format_is_json() -> None:
    """config_format must be 'json' (Claude Desktop config is JSON)."""
    assert CLAUDE_CONTRACT.config_format == "json"


def test_claude_contract_representative_config_path_is_set() -> None:
    """representative_config_path must be a non-empty Path."""
    path = CLAUDE_CONTRACT.representative_config_path
    assert path != Path()
    assert str(path)


def test_representative_config_path_has_correct_filename() -> None:
    """representative_config_path.name must equal CLAUDE_CONFIG_FILENAME."""
    assert CLAUDE_CONTRACT.representative_config_path.name == CLAUDE_CONFIG_FILENAME
    config_filename = "claude_desktop_config.json"
    assert CLAUDE_CONTRACT.representative_config_path.name == config_filename


def test_claude_contract_representative_config_path_includes_claude_dir() -> None:
    """representative_config_path must include a 'Claude' parent directory."""
    path_parts = CLAUDE_CONTRACT.representative_config_path.parts
    assert "Claude" in path_parts


def test_contract_has_no_target_config_path_field() -> None:
    """AgentTargetContract must not expose a 'target_config_path' field.

    The field was renamed to 'representative_config_path' to prevent install
    code from accidentally writing to the macOS-representative path on Windows
    or Linux.  If this attribute exists, install code could silently use it
    and write to the wrong location on non-macOS platforms.
    """
    assert not hasattr(CLAUDE_CONTRACT, "target_config_path"), (
        "AgentTargetContract must not have a 'target_config_path' attribute; "
        "the field was renamed to 'representative_config_path' to prevent "
        "install code from reading it directly on non-macOS platforms."
    )


# ---------------------------------------------------------------------------
# MCP transport
# ---------------------------------------------------------------------------


def test_claude_contract_mcp_transport_is_sse() -> None:
    """mcp_transport must be 'sse' (modal-mcp is an HTTP/ASGI server)."""
    assert CLAUDE_CONTRACT.mcp_transport == "sse"
    assert CLAUDE_CONTRACT.mcp_transport == CLAUDE_TRANSPORT


def test_claude_contract_server_name() -> None:
    """server_name must be 'modal-mcp'."""
    assert CLAUDE_CONTRACT.server_name == "modal-mcp"
    assert CLAUDE_CONTRACT.server_name == CLAUDE_SERVER_NAME


def test_claude_contract_top_level_key() -> None:
    """MCP servers are registered under the 'mcpServers' top-level key."""
    assert CLAUDE_CONTRACT.top_level_key == "mcpServers"
    assert CLAUDE_CONTRACT.top_level_key == CLAUDE_TOP_LEVEL_KEY


# ---------------------------------------------------------------------------
# SSE transport consistency
# ---------------------------------------------------------------------------


def test_claude_print_config_contains_transport_and_url() -> None:
    """Contract declares a concrete SSE URL and no stdio command.

    The install/print code can produce a complete config snippet from the
    contract fields alone, with no further research required.
    """
    assert CLAUDE_CONTRACT.server_url is not None
    assert CLAUDE_CONTRACT.server_url.startswith("http://")
    assert CLAUDE_MCP_SSE_PATH in CLAUDE_CONTRACT.server_url
    # SSE transport: no command or args
    assert CLAUDE_CONTRACT.server_command is None
    assert CLAUDE_CONTRACT.server_args_template == ()


def test_claude_contract_server_url_targets_local_mcp_sse_endpoint() -> None:
    """server_url must point to the default local MCP/SSE endpoint."""
    assert CLAUDE_CONTRACT.server_url == CLAUDE_SSE_URL
    assert CLAUDE_CONTRACT.server_url == f"http://{CLAUDE_DEFAULT_BIND}/mcp/sse"


def test_claude_contract_server_url_contains_default_bind() -> None:
    """server_url must contain the default bind address."""
    assert CLAUDE_DEFAULT_BIND in (CLAUDE_CONTRACT.server_url or "")


# ---------------------------------------------------------------------------
# Cwd / env-file behaviour
# ---------------------------------------------------------------------------


def test_claude_command_snippet_uses_absolute_env_file_or_cwd() -> None:
    """SSE transport: env-file is not injected into the Claude Desktop config.

    Claude Desktop connects to the server via SSE; it does not launch
    modal-mcp.  Therefore env-file handling is 'not_applicable'.
    """
    assert CLAUDE_CONTRACT.env_file_strategy == "not_applicable"
    assert not CLAUDE_CONTRACT.supports_cwd_config


def test_claude_contract_env_file_strategy_is_not_applicable() -> None:
    """env_file_strategy must be 'not_applicable' for SSE transport."""
    assert CLAUDE_CONTRACT.env_file_strategy == "not_applicable"


# ---------------------------------------------------------------------------
# format_startup_command helper
# ---------------------------------------------------------------------------


def test_claude_command_format_uses_absolute_env_file(tmp_path: Path) -> None:
    """format_startup_command() must produce a command with an absolute path."""
    abs_env = str(tmp_path / ".env")
    cmd = format_startup_command(abs_env)
    assert "--env-file" in cmd
    env_file_idx = list(cmd).index("--env-file")
    resolved = cmd[env_file_idx + 1]
    assert Path(resolved).is_absolute()
    assert resolved == abs_env


def test_format_startup_command_rejects_relative_path() -> None:
    """format_startup_command() must raise ValueError for relative paths."""
    with pytest.raises(ValueError, match="absolute path"):
        format_startup_command("relative/.env")


def test_format_startup_command_accepts_path_object(tmp_path: Path) -> None:
    """format_startup_command() must accept a Path as well as a string."""
    abs_path = tmp_path / ".env"
    cmd = format_startup_command(abs_path)
    assert str(abs_path) in cmd


def test_format_startup_command_includes_env_file_flag(tmp_path: Path) -> None:
    """format_startup_command() output must include CLAUDE_ENV_FILE_FLAG."""
    cmd = format_startup_command(str(tmp_path / ".env"))
    assert CLAUDE_ENV_FILE_FLAG in cmd


def test_format_startup_command_starts_with_modal_mcp(tmp_path: Path) -> None:
    """The startup command must start with 'modal-mcp'."""
    env_file = tmp_path / ".env"
    cmd = format_startup_command(str(env_file))
    assert cmd[0] == "modal-mcp"


# ---------------------------------------------------------------------------
# Merge key and idempotency
# ---------------------------------------------------------------------------


def test_claude_contract_idempotency_key() -> None:
    """idempotency_key must encode the full dotted path to the server entry."""
    assert CLAUDE_CONTRACT.idempotency_key == "mcpServers.modal-mcp"
    assert CLAUDE_CONTRACT.idempotency_key == CLAUDE_IDEMPOTENCY_KEY


def test_claude_contract_merge_key_components_are_consistent() -> None:
    """idempotency_key must be composed of top_level_key and server_name."""
    parts = CLAUDE_CONTRACT.idempotency_key.split(".")
    assert len(parts) == 2
    assert parts[0] == CLAUDE_CONTRACT.top_level_key
    assert parts[1] == CLAUDE_CONTRACT.server_name


# ---------------------------------------------------------------------------
# Backup naming
# ---------------------------------------------------------------------------


def test_claude_contract_backup_suffix_has_timestamp_placeholder() -> None:
    """backup_suffix_template must contain a {timestamp} placeholder."""
    assert "{timestamp}" in CLAUDE_CONTRACT.backup_suffix_template
    assert "{timestamp}" in CLAUDE_BACKUP_SUFFIX_TEMPLATE


def test_claude_contract_backup_suffix_starts_with_dot() -> None:
    """backup_suffix_template must start with a dot."""
    assert CLAUDE_CONTRACT.backup_suffix_template.startswith(".")


def test_backup_suffix_template_produces_valid_backup_name() -> None:
    """Substituting a compact timestamp into the template must produce a valid filename.

    The compact ``YYYYMMDDTHHmmss`` format (no colons, no spaces) is required so
    that the resulting filename is valid on Windows NTFS/FAT32, which forbids
    the ``:`` character in filenames.
    """
    # Compact ISO format: no colons — safe on Windows NTFS/FAT32.
    timestamp = "20260419T120000"
    backup_name = CLAUDE_CONFIG_FILENAME + CLAUDE_BACKUP_SUFFIX_TEMPLATE.format(
        timestamp=timestamp
    )
    assert backup_name == "claude_desktop_config.json.bak.20260419T120000"


def test_backup_suffix_template_produces_filesystem_safe_name() -> None:
    """The substituted backup filename must not contain characters illegal on Windows.

    Windows NTFS/FAT32 forbid: ``\\ / : * ? " < > |``.
    The compact timestamp format (``YYYYMMDDTHHmmss``) satisfies this constraint.
    Since Claude Desktop ships on Windows and the contract declares a Windows path,
    the backup step must not emit a colon-containing filename.
    """
    # Simulate the compact-format timestamp an installer would supply.
    timestamp = "20260419T103000"
    backup_name = CLAUDE_CONFIG_FILENAME + CLAUDE_BACKUP_SUFFIX_TEMPLATE.format(
        timestamp=timestamp
    )
    # Characters illegal on Windows NTFS/FAT32
    _WINDOWS_ILLEGAL = set('\\/:*?"<>|')
    illegal_found = _WINDOWS_ILLEGAL & set(backup_name)
    assert not illegal_found, (
        f"Backup filename {backup_name!r} contains Windows-illegal characters: "
        f"{illegal_found!r}.  Use a compact timestamp without ':' (e.g. "
        f"'20260419T103000' instead of '2026-04-19T10:30:00')."
    )


# ---------------------------------------------------------------------------
# Parse-validation strategy
# ---------------------------------------------------------------------------


def test_claude_contract_parse_validation_strategy_references_json() -> None:
    """parse_validation_strategy must mention JSON parsing."""
    strategy = CLAUDE_CONTRACT.parse_validation_strategy.lower()
    assert "json" in strategy


def test_claude_contract_parse_validation_strategy_mentions_modal_mcp() -> None:
    """parse_validation_strategy must reference the server entry being validated."""
    strategy = CLAUDE_CONTRACT.parse_validation_strategy
    assert "modal-mcp" in strategy


def test_claude_contract_parse_validation_strategy_mentions_backup() -> None:
    """parse_validation_strategy must mention backup/restore on failure."""
    strategy = CLAUDE_CONTRACT.parse_validation_strategy.lower()
    assert "backup" in strategy or "restore" in strategy


# ---------------------------------------------------------------------------
# Dry-run description
# ---------------------------------------------------------------------------


def test_claude_install_dry_run_prints_target_and_change() -> None:
    """dry_run_description must reference the target file and the change."""
    desc = CLAUDE_CONTRACT.dry_run_description
    assert "claude_desktop_config.json" in desc
    assert "modal-mcp" in desc
    assert "sse" in desc.lower() or "http" in desc.lower()


# ---------------------------------------------------------------------------
# Refusal conditions — original four
# ---------------------------------------------------------------------------


def test_claude_contract_refusal_conditions_is_non_empty() -> None:
    """refusal_conditions must be a non-empty tuple of strings."""
    assert isinstance(CLAUDE_CONTRACT.refusal_conditions, tuple)
    assert len(CLAUDE_CONTRACT.refusal_conditions) >= 3
    assert all(isinstance(c, str) and c for c in CLAUDE_CONTRACT.refusal_conditions)


def test_claude_contract_refusal_conditions_cover_missing_config_dir() -> None:
    """At least one refusal condition must address a missing config directory."""
    joined = " ".join(CLAUDE_CONTRACT.refusal_conditions).lower()
    assert "directory" in joined or "not installed" in joined or "not found" in joined


def test_claude_contract_refusal_conditions_cover_symlink() -> None:
    """At least one refusal condition must address symlink targets."""
    joined = " ".join(CLAUDE_CONTRACT.refusal_conditions).lower()
    assert "symlink" in joined


def test_claude_contract_refusal_conditions_cover_unparseable_json() -> None:
    """At least one refusal condition must address JSON parse failure."""
    joined = " ".join(CLAUDE_CONTRACT.refusal_conditions).lower()
    assert "json" in joined or "parse" in joined


def test_claude_install_refuses_unparseable_config() -> None:
    """Contract must list a refusal condition for unparseable config.

    This verifies the *contract* declares the refusal condition; the actual
    install guard is implemented in a later ticket.
    """
    unparseable_condition = next(
        (
            c
            for c in CLAUDE_CONTRACT.refusal_conditions
            if "json" in c.lower() or "parse" in c.lower()
        ),
        None,
    )
    assert unparseable_condition is not None, (
        "CLAUDE_CONTRACT must list a refusal condition for unparseable config"
    )


# ---------------------------------------------------------------------------
# Refusal conditions — additional failure modes (finding-5)
# ---------------------------------------------------------------------------


def test_claude_contract_refusal_conditions_cover_permission_denied() -> None:
    """At least one refusal condition must address permission-denied errors.

    On macOS it is common for the Claude Desktop config to be owned by a
    different UID after a user switch.  The installer must refuse rather than
    crash or silently write to the wrong location.
    """
    joined = " ".join(CLAUDE_CONTRACT.refusal_conditions).lower()
    assert "permission" in joined or "read" in joined or "writable" in joined, (
        "CLAUDE_CONTRACT must include a refusal condition for permission-denied "
        "on read or write (e.g. file owned by another UID after a user switch)"
    )


def test_claude_contract_refusal_conditions_cover_disk_full() -> None:
    """At least one refusal condition must address disk-full / ENOSPC errors.

    The installer performs an atomic write via a temp file; if the disk is
    full the write will fail at the OS level.  The contract must declare this
    as a refusal case so install code handles it explicitly.
    """
    joined = " ".join(CLAUDE_CONTRACT.refusal_conditions).lower()
    assert "disk" in joined or "enospc" in joined or "space" in joined, (
        "CLAUDE_CONTRACT must include a refusal condition for disk full / ENOSPC"
    )


def test_claude_contract_refusal_conditions_cover_non_regular_file() -> None:
    """At least one refusal condition must address non-regular-file config targets.

    If the config path is a directory, FIFO, socket, or device node the write
    must be refused rather than attempted.
    """
    joined = " ".join(CLAUDE_CONTRACT.refusal_conditions).lower()
    assert (
        "directory" in joined
        or "fifo" in joined
        or "socket" in joined
        or "regular file" in joined
        or "device" in joined
    ), (
        "CLAUDE_CONTRACT must include a refusal condition for non-regular-file "
        "config targets (directory, FIFO, socket, device node)"
    )


def test_claude_contract_refusal_conditions_cover_conflicting_entry() -> None:
    """At least one refusal condition must address a conflicting existing entry.

    If ``mcpServers.modal-mcp`` already exists with a different transport or
    URL (e.g. a stdio entry) the installer must refuse rather than silently
    overwrite the user's existing configuration.
    """
    joined = " ".join(CLAUDE_CONTRACT.refusal_conditions).lower()
    assert "conflict" in joined or "incompatible" in joined or "exist" in joined, (
        "CLAUDE_CONTRACT must include a refusal condition for a pre-existing "
        "mcpServers.modal-mcp entry with an incompatible transport or url"
    )


def test_claude_contract_has_at_least_eight_refusal_conditions() -> None:
    """refusal_conditions must cover all declared failure modes (at least eight).

    The contract must document: (1) missing config dir, (2) symlink,
    (3) unparseable JSON, (4) round-trip failure, (5) permission denied,
    (6) disk full, (7) non-regular file, (8) conflicting entry.
    """
    assert len(CLAUDE_CONTRACT.refusal_conditions) >= 8, (
        f"Expected >= 8 refusal conditions, got "
        f"{len(CLAUDE_CONTRACT.refusal_conditions)}"
    )


def test_claude_install_creates_backup_and_preserves_unrelated_config() -> None:
    """Contract must declare backup behaviour.

    The actual backup creation is in a later install ticket; this test
    confirms the contract specifies the required backup suffix template.
    """
    assert CLAUDE_CONTRACT.backup_suffix_template
    assert "{timestamp}" in CLAUDE_CONTRACT.backup_suffix_template


def test_claude_install_is_idempotent() -> None:
    """Contract must declare an idempotency key for detecting existing registrations."""
    assert CLAUDE_CONTRACT.idempotency_key == "mcpServers.modal-mcp"


# ---------------------------------------------------------------------------
# Platform-specific config paths
# ---------------------------------------------------------------------------


def test_get_claude_config_dir_darwin() -> None:
    """On macOS, config dir must be ~/Library/Application Support/Claude."""
    with patch.object(sys, "platform", "darwin"):
        result = get_claude_config_dir()
    assert result is not None
    assert result == Path.home() / "Library" / "Application Support" / "Claude"


def test_get_claude_config_dir_win32_with_appdata() -> None:
    """On Windows with APPDATA set, config dir must use APPDATA."""
    with (
        patch.object(sys, "platform", "win32"),
        patch.dict("os.environ", {"APPDATA": "C:\\Users\\User\\AppData\\Roaming"}),
    ):
        result = get_claude_config_dir()
    assert result is not None
    assert result == Path("C:\\Users\\User\\AppData\\Roaming") / "Claude"


def test_get_claude_config_dir_linux_with_xdg() -> None:
    """On Linux with XDG_CONFIG_HOME set, config dir must use it."""
    with (
        patch.object(sys, "platform", "linux"),
        patch.dict("os.environ", {"XDG_CONFIG_HOME": "/home/user/.config2"}),
    ):
        result = get_claude_config_dir()
    assert result is not None
    assert result == Path("/home/user/.config2") / "Claude"


def test_get_claude_config_dir_linux_fallback() -> None:
    """On Linux without XDG_CONFIG_HOME, config dir must use ~/.config."""
    import os

    original_get = os.environ.get

    def mock_env_get(key: str, default: str | None = None) -> str | None:
        if key == "XDG_CONFIG_HOME":
            return None
        return original_get(key, default)

    with (
        patch.object(sys, "platform", "linux"),
        patch("os.environ.get", side_effect=mock_env_get),
    ):
        result = get_claude_config_dir()
    assert result is not None
    assert result == Path.home() / ".config" / "Claude"


def test_get_claude_config_dir_unsupported_platform() -> None:
    """On an unsupported platform, get_claude_config_dir() must return None."""
    with patch.object(sys, "platform", "freebsd14"):
        result = get_claude_config_dir()
    assert result is None


def test_get_claude_config_path_uses_correct_filename() -> None:
    """get_claude_config_path() must append the canonical config filename."""
    with patch.object(sys, "platform", "darwin"):
        result = get_claude_config_path()
    assert result is not None
    assert result.name == CLAUDE_CONFIG_FILENAME


def test_repr_config_path_matches_get_config_path_on_darwin() -> None:
    """On macOS, get_claude_config_path() must equal
    representative_config_path.expanduser().

    ``AgentTargetContract.representative_config_path`` is documented as a
    representative macOS path.  Install code must call
    ``get_claude_config_path()`` rather than reading
    ``representative_config_path`` directly (which is unexpanded and
    platform-specific).  This test closes the dual-source-of-truth gap by
    asserting that the two values agree on the platform they represent.
    """
    with patch.object(sys, "platform", "darwin"):
        runtime_path = get_claude_config_path()
    assert runtime_path is not None
    assert runtime_path == CLAUDE_CONTRACT.representative_config_path.expanduser(), (
        "On macOS, get_claude_config_path() and "
        "CLAUDE_CONTRACT.representative_config_path.expanduser() must resolve to "
        "the same path.  If they diverge, either the contract representative value "
        "or get_claude_config_path() needs updating."
    )


def test_contract_representative_config_path_diverges_on_non_darwin() -> None:
    """On Windows, get_claude_config_path() must differ from representative_config_path.

    CLAUDE_CONTRACT.representative_config_path stores the macOS path as a
    representative value only.  Install code must NEVER read
    representative_config_path directly to determine where to write the config
    on non-macOS systems; it MUST call get_claude_config_path() at runtime.
    This test asserts the divergence so that any install implementer who uses
    representative_config_path directly on Windows would write to a silently
    wrong location and this test would catch it.
    """
    darwin_representative = CLAUDE_CONTRACT.representative_config_path.expanduser()

    with (
        patch.object(sys, "platform", "win32"),
        patch.dict("os.environ", {"APPDATA": "C:\\Users\\TestUser\\AppData\\Roaming"}),
    ):
        win_path = get_claude_config_path()

    assert win_path is not None, "Windows must have a supported config path"
    assert win_path != darwin_representative, (
        "On Windows, get_claude_config_path() must return a different path "
        "than CLAUDE_CONTRACT.representative_config_path.expanduser().  "
        "Install code MUST call get_claude_config_path() at runtime."
    )


def test_get_claude_config_path_unsupported_platform() -> None:
    """get_claude_config_path() must return None on unsupported platforms."""
    with patch.object(sys, "platform", "freebsd14"):
        result = get_claude_config_path()
    assert result is None


# ---------------------------------------------------------------------------
# build_contract()
# ---------------------------------------------------------------------------


def test_build_contract_returns_contract_instance() -> None:
    """build_contract() must return an AgentTargetContract."""
    contract = build_contract()
    assert isinstance(contract, AgentTargetContract)


def test_build_contract_custom_bind_updates_url() -> None:
    """build_contract() with a custom bind must update server_url."""
    contract = build_contract("localhost:9876")
    assert "localhost:9876" in (contract.server_url or "")
    assert "/mcp/sse" in (contract.server_url or "")


def test_build_contract_custom_bind_updates_all_url_bearing_fields() -> None:
    """build_contract() must propagate bind to ALL fields that embed the URL.

    Install code MUST call build_contract(bind=Settings.bind) rather than
    using the module-level CLAUDE_CONTRACT singleton when the user's Settings
    specifies a non-default bind address.  Otherwise parse_validation_strategy
    and dry_run_description would reference a URL the server is not listening on.
    """
    custom_bind = "0.0.0.0:9999"
    contract = build_contract(custom_bind)
    expected_url = f"http://{custom_bind}/mcp/sse"

    assert contract.server_url == expected_url, (
        "server_url must reflect the custom bind address"
    )
    assert custom_bind in contract.parse_validation_strategy, (
        "parse_validation_strategy must embed the runtime server URL so that "
        "install code can use it directly"
    )
    assert custom_bind in contract.dry_run_description, (
        "dry_run_description must embed the runtime server URL so that "
        "dry-run output shows the actual registered URL"
    )


def test_build_contract_immutable() -> None:
    """Returned AgentTargetContract must be immutable (frozen dataclass)."""
    contract = build_contract()
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        contract.mcp_transport = "stdio"  # type: ignore[misc]


def test_install_code_must_call_build_contract_not_use_singleton() -> None:
    """Contractual requirement: install code MUST call
    build_contract(bind=settings.bind).

    CLAUDE_CONTRACT is frozen with CLAUDE_DEFAULT_BIND.  If a user runs
    ``modal-mcp run --bind 10.0.0.5:9000``, install code that imports
    CLAUDE_CONTRACT instead of calling build_contract(bind=settings.bind) will
    register ``http://127.0.0.1:8765/mcp/sse`` — a URL the server is NOT
    listening on.  This test explicitly documents and enforces the requirement
    that install code must call build_contract(bind=settings.bind) so that all
    URL-bearing contract fields reflect the actual server address.
    """
    # Simulate Settings.bind != CLAUDE_DEFAULT_BIND
    custom_bind = "10.0.0.5:9000"
    install_contract = build_contract(bind=custom_bind)

    # The install contract's URL must differ from the singleton's URL
    assert install_contract.server_url != CLAUDE_CONTRACT.server_url, (
        "build_contract(bind=custom) must produce a different server_url than "
        "the CLAUDE_CONTRACT singleton; if they were the same, install code "
        "could accidentally use the singleton for non-default binds without "
        "any test catching it"
    )

    # All URL-bearing fields must reflect the custom bind, not the default
    custom_url = f"http://{custom_bind}/mcp/sse"
    assert install_contract.server_url == custom_url
    assert custom_bind in install_contract.parse_validation_strategy
    assert custom_bind in install_contract.dry_run_description
    # The singleton must still carry the default URL (not contaminated)
    assert CLAUDE_DEFAULT_BIND in (CLAUDE_CONTRACT.server_url or "")


# ---------------------------------------------------------------------------
# Contract completeness: enough detail to implement install
# ---------------------------------------------------------------------------


def test_contract_has_enough_detail_for_install() -> None:
    """All fields needed to implement install without further research must be set.

    This is a high-level gate: if any new required field is added to
    AgentTargetContract its absence here will cause a dataclasses error, and
    this test ensures the values are substantive.
    """
    c = CLAUDE_CONTRACT

    # Transport is fully specified (server_url must be set for SSE)
    assert c.server_url is not None, "server_url must be set for SSE transport"
    assert c.server_url.startswith("http://")

    # Merge path is unambiguous
    assert c.top_level_key
    assert c.server_name
    assert c.idempotency_key

    # Backup can be computed
    assert "{timestamp}" in c.backup_suffix_template

    # Refusal conditions are comprehensive
    assert len(c.refusal_conditions) >= 8

    # Validation strategy is actionable
    assert len(c.parse_validation_strategy) >= 20

    # Dry-run is informative
    assert c.dry_run_description

    # Config location and format are explicit
    assert c.config_format == "json"
    assert c.representative_config_path != Path()


# ===========================================================================
# format_config_snippet()
# ===========================================================================


def test_format_config_snippet_returns_string() -> None:
    """format_config_snippet() must return a non-empty string."""
    from modal_mcp.agent_targets.claude import format_config_snippet

    snippet = format_config_snippet()
    assert isinstance(snippet, str)
    assert snippet


def test_format_config_snippet_is_valid_json() -> None:
    """format_config_snippet() must produce valid JSON."""
    import json

    from modal_mcp.agent_targets.claude import format_config_snippet

    snippet = format_config_snippet()
    parsed = json.loads(snippet)
    assert isinstance(parsed, dict)


def test_format_config_snippet_contains_mcp_servers_key() -> None:
    """Snippet must contain the mcpServers top-level key."""
    import json

    from modal_mcp.agent_targets.claude import (
        CLAUDE_TOP_LEVEL_KEY,
        format_config_snippet,
    )

    snippet = format_config_snippet()
    parsed = json.loads(snippet)
    assert CLAUDE_TOP_LEVEL_KEY in parsed


def test_format_config_snippet_contains_modal_mcp_entry() -> None:
    """Snippet must contain the modal-mcp server entry under mcpServers."""
    import json

    from modal_mcp.agent_targets.claude import (
        CLAUDE_SERVER_NAME,
        CLAUDE_TOP_LEVEL_KEY,
        format_config_snippet,
    )

    snippet = format_config_snippet()
    parsed = json.loads(snippet)
    assert CLAUDE_SERVER_NAME in parsed[CLAUDE_TOP_LEVEL_KEY]


def test_format_config_snippet_contains_sse_type() -> None:
    """Snippet entry must declare type: sse."""
    import json

    from modal_mcp.agent_targets.claude import (
        CLAUDE_SERVER_NAME,
        CLAUDE_TOP_LEVEL_KEY,
        CLAUDE_TRANSPORT,
        format_config_snippet,
    )

    snippet = format_config_snippet()
    parsed = json.loads(snippet)
    entry = parsed[CLAUDE_TOP_LEVEL_KEY][CLAUDE_SERVER_NAME]
    assert entry["type"] == CLAUDE_TRANSPORT
    assert entry["type"] == "sse"


def test_format_config_snippet_contains_server_url() -> None:
    """Snippet entry must declare the SSE url."""
    import json

    from modal_mcp.agent_targets.claude import (
        CLAUDE_SERVER_NAME,
        CLAUDE_SSE_URL,
        CLAUDE_TOP_LEVEL_KEY,
        format_config_snippet,
    )

    snippet = format_config_snippet()
    parsed = json.loads(snippet)
    entry = parsed[CLAUDE_TOP_LEVEL_KEY][CLAUDE_SERVER_NAME]
    assert entry["url"] == CLAUDE_SSE_URL
    assert entry["url"].startswith("http://")


def test_format_config_snippet_does_not_contain_secrets() -> None:
    """format_config_snippet() must not contain secret-related keywords."""
    from modal_mcp.agent_targets.claude import format_config_snippet

    snippet = format_config_snippet().lower()
    secret_patterns = ("password", "token", "api_key", "secret", "credential")
    for pattern in secret_patterns:
        assert pattern not in snippet, (
            f"Snippet must not contain secret-related keyword: {pattern!r}"
        )


def test_format_config_snippet_no_command_field() -> None:
    """Snippet must not include a command field (SSE transport, not stdio)."""
    from modal_mcp.agent_targets.claude import format_config_snippet

    snippet = format_config_snippet()
    assert "command" not in snippet, (
        "Claude uses SSE transport;"
        " the config snippet must not include a 'command' field"
    )


# ===========================================================================
# print_agent_config() — Claude target
# ===========================================================================


def test_print_agent_config_claude_returns_none() -> None:
    """print_agent_config('claude') must return None (side-effect only)."""
    import io

    from modal_mcp.agent_config import print_agent_config

    buf = io.StringIO()
    result = print_agent_config("claude", file=buf)
    assert result is None


def test_print_agent_config_claude_writes_to_file_arg() -> None:
    """print_agent_config('claude') must write to the supplied file argument."""
    import io

    from modal_mcp.agent_config import print_agent_config

    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    output = buf.getvalue()
    assert output, "Output must be non-empty"


def test_print_agent_config_claude_output_contains_json_block() -> None:
    """print_agent_config('claude') must include a JSON config block."""
    import io
    import json

    from modal_mcp.agent_config import print_agent_config

    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    output = buf.getvalue()
    # Extract non-comment lines and parse as JSON
    json_lines = [ln for ln in output.splitlines() if not ln.startswith("#")]
    json_text = "\n".join(json_lines).strip()
    parsed = json.loads(json_text)
    assert isinstance(parsed, dict)


def test_print_agent_config_claude_output_contains_mcp_servers_key() -> None:
    """print_agent_config('claude') output must include mcpServers key."""
    import io

    from modal_mcp.agent_config import print_agent_config

    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    output = buf.getvalue()
    assert "mcpServers" in output


def test_print_agent_config_claude_output_contains_modal_mcp_entry() -> None:
    """print_agent_config('claude') output must include modal-mcp entry."""
    import io

    from modal_mcp.agent_config import print_agent_config

    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    output = buf.getvalue()
    assert "modal-mcp" in output


def test_print_agent_config_claude_output_states_sse_or_http_transport() -> None:
    """Output must state whether this is HTTP or command-launched MCP."""
    import io

    from modal_mcp.agent_config import print_agent_config

    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    output_lower = buf.getvalue().lower()
    assert "sse" in output_lower or "http" in output_lower, (
        "Output must state whether the transport is HTTP/SSE or stdio so users "
        "understand whether a running server is required"
    )


def test_print_agent_config_claude_output_contains_server_url() -> None:
    """print_agent_config('claude') output must include the SSE URL."""
    import io

    from modal_mcp.agent_config import print_agent_config
    from modal_mcp.agent_targets.claude import CLAUDE_SSE_URL

    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    output = buf.getvalue()
    assert CLAUDE_SSE_URL in output


def test_print_agent_config_claude_output_contains_startup_command() -> None:
    """Output must include a startup command for starting the server."""
    import io

    from modal_mcp.agent_config import print_agent_config

    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    output = buf.getvalue()
    assert "modal-mcp run" in output, (
        "Output must include the startup command 'modal-mcp run' so the user "
        "knows to start the server before Claude Desktop connects"
    )


def test_print_agent_config_claude_startup_command_uses_absolute_env_file() -> None:
    """The startup command in the output must use an absolute --env-file path.

    Claude Desktop does not launch modal-mcp; the user must start the server
    separately.  The startup command hint must use an absolute --env-file path
    (or a clearly absolute placeholder) so that the server finds its settings
    regardless of the shell's working directory.
    """
    import io
    import re

    from modal_mcp.agent_config import print_agent_config

    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    output = buf.getvalue()
    # Find the line(s) containing the actual startup command (not just a comment
    # mentioning the flag name)
    cmd_lines = [
        ln for ln in output.splitlines() if "modal-mcp run" in ln and "--env-file" in ln
    ]
    assert cmd_lines, (
        "Output must contain a startup command line with modal-mcp run --env-file"
    )
    for line in cmd_lines:
        # Extract the path argument that follows --env-file
        match = re.search(r"--env-file\s+(\S+)", line)
        assert match, f"Could not parse --env-file path from: {line!r}"
        env_path = match.group(1)
        assert env_path.startswith("/"), (
            f"env-file path in startup command must be absolute (start with '/'); "
            f"got: {env_path!r}"
        )


def test_print_agent_config_claude_with_env_file(
    tmp_path: Path,
) -> None:
    """print_agent_config(..., env_file=...) must embed the given path."""
    import io

    from modal_mcp.agent_config import print_agent_config

    abs_env = str(tmp_path / ".env")
    buf = io.StringIO()
    print_agent_config("claude", env_file=abs_env, file=buf)
    output = buf.getvalue()
    assert abs_env in output, (
        "The supplied env_file path must appear in the startup command hint"
    )


def test_print_agent_config_claude_with_path_object_env_file(
    tmp_path: Path,
) -> None:
    """print_agent_config('claude', env_file=Path(...)) must accept Path objects."""
    import io

    from modal_mcp.agent_config import print_agent_config

    abs_env = tmp_path / ".env"
    buf = io.StringIO()
    print_agent_config("claude", env_file=abs_env, file=buf)
    output = buf.getvalue()
    assert str(abs_env) in output


def test_print_agent_config_claude_rejects_relative_env_file() -> None:
    """print_agent_config('claude') must raise ValueError for a relative env_file."""
    import io

    from modal_mcp.agent_config import print_agent_config

    with pytest.raises(ValueError, match="absolute path"):
        print_agent_config("claude", env_file="relative/.env", file=io.StringIO())


def test_print_agent_config_claude_does_not_leak_secrets() -> None:
    """print_agent_config('claude') output must not contain secret keywords."""
    import io

    from modal_mcp.agent_config import print_agent_config

    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    output_lower = buf.getvalue().lower()
    secret_patterns = ("password", "token", "api_key", "secret", "credential")
    for pattern in secret_patterns:
        assert pattern not in output_lower, (
            f"Output must not contain secret-related keyword: {pattern!r}"
        )


def test_print_agent_config_claude_does_not_write_files(tmp_path: Path) -> None:
    """print_agent_config('claude') must not create or modify any files."""
    import io

    from modal_mcp.agent_config import print_agent_config

    before = set(tmp_path.iterdir())
    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    after = set(tmp_path.iterdir())
    assert before == after, (
        "print_agent_config must not write any files; "
        f"new files found: {after - before}"
    )


def test_print_agent_config_claude_case_insensitive() -> None:
    """Target name matching must be case-insensitive for claude."""
    import io

    from modal_mcp.agent_config import print_agent_config

    buf1 = io.StringIO()
    buf2 = io.StringIO()
    buf3 = io.StringIO()
    print_agent_config("claude", file=buf1)
    print_agent_config("CLAUDE", file=buf2)
    print_agent_config("Claude_Desktop", file=buf3)
    assert buf1.getvalue() == buf2.getvalue()
    assert buf1.getvalue() == buf3.getvalue()


def test_print_agent_config_claude_desktop_alias() -> None:
    """'claude_desktop' must produce the same output as 'claude'."""
    import io

    from modal_mcp.agent_config import print_agent_config

    buf1 = io.StringIO()
    buf2 = io.StringIO()
    print_agent_config("claude", file=buf1)
    print_agent_config("claude_desktop", file=buf2)
    assert buf1.getvalue() == buf2.getvalue()


def test_print_agent_config_claude_output_json_is_structurally_complete() -> None:
    """The JSON block in the output must contain the full required structure.

    The emitted snippet must be a complete, ready-to-paste config object.
    """
    import io
    import json

    from modal_mcp.agent_config import print_agent_config
    from modal_mcp.agent_targets.claude import (
        CLAUDE_SERVER_NAME,
        CLAUDE_SSE_URL,
        CLAUDE_TOP_LEVEL_KEY,
        CLAUDE_TRANSPORT,
    )

    buf = io.StringIO()
    print_agent_config("claude", file=buf)
    output = buf.getvalue()

    # Parse JSON portion (skip comment lines)
    json_lines = [ln for ln in output.splitlines() if not ln.startswith("#")]
    parsed = json.loads("\n".join(json_lines).strip())

    # Verify the full structure
    assert CLAUDE_TOP_LEVEL_KEY in parsed
    servers = parsed[CLAUDE_TOP_LEVEL_KEY]
    assert CLAUDE_SERVER_NAME in servers
    entry = servers[CLAUDE_SERVER_NAME]
    assert entry["type"] == CLAUDE_TRANSPORT
    assert entry["url"] == CLAUDE_SSE_URL


# ===========================================================================
# install_claude_config() — actual install mechanics
# ===========================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_claude_dir(tmp_path: Path) -> Path:
    """Create a temporary Claude Desktop config directory and return it."""
    claude_dir = tmp_path / "Claude"
    claude_dir.mkdir()
    return claude_dir


def _claude_config(claude_dir: Path) -> Path:
    """Return the path to claude_desktop_config.json within a Claude dir."""
    return claude_dir / CLAUDE_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_install_claude_dry_run_returns_dry_run(tmp_path: Path) -> None:
    """install_claude_config(dry_run=True) returns 'dry_run'."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    result = install_claude_config(
        dry_run=True,
        config_path=cfg,
        file=_io.StringIO(),
    )
    assert result == "dry_run"


def test_install_claude_dry_run_prints_target_file(tmp_path: Path) -> None:
    """setup --install claude --dry-run: output contains the target file path."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    buf = _io.StringIO()
    install_claude_config(dry_run=True, config_path=cfg, file=buf)
    output = buf.getvalue()
    assert str(cfg) in output, "Dry-run output must include the target config file path"


def test_install_claude_dry_run_prints_exact_change(tmp_path: Path) -> None:
    """setup --install claude --dry-run: output describes the exact change."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    buf = _io.StringIO()
    install_claude_config(dry_run=True, config_path=cfg, file=buf)
    output = buf.getvalue()
    # Must mention the mcpServers entry with sse transport
    assert "modal-mcp" in output
    assert "sse" in output.lower() or "http" in output.lower()
    # Must contain the SSE URL
    assert CLAUDE_SSE_URL in output


def test_install_claude_dry_run_does_not_write_files(tmp_path: Path) -> None:
    """Dry-run must not create or modify any files."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    before = set(claude_dir.iterdir())
    install_claude_config(dry_run=True, config_path=cfg, file=_io.StringIO())
    after = set(claude_dir.iterdir())
    assert before == after, f"Dry-run must not write files; new: {after - before}"


def test_install_claude_dry_run_output_contains_json_block(tmp_path: Path) -> None:
    """Dry-run output contains a parseable JSON block with the expected structure."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    buf = _io.StringIO()
    install_claude_config(dry_run=True, config_path=cfg, file=buf)
    output = buf.getvalue()
    # Extract non-header lines
    json_lines = [
        ln
        for ln in output.splitlines()
        if not ln.startswith("Target:")
        and not ln.startswith("Change:")
        and not ln.startswith("Would add")
    ]
    json_text = "\n".join(json_lines).strip()
    if json_text:
        parsed = _json_module.loads(json_text)
        assert CLAUDE_TOP_LEVEL_KEY in parsed


# ---------------------------------------------------------------------------
# Happy path — fresh install
# ---------------------------------------------------------------------------


def test_install_claude_returns_installed_on_success(tmp_path: Path) -> None:
    """install_claude_config returns 'installed' when config is written."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    result = install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())
    assert result == "installed"


def test_install_claude_creates_config_when_absent(tmp_path: Path) -> None:
    """install_claude_config creates claude_desktop_config.json when absent."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    assert not cfg.exists()
    install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())
    assert cfg.is_file()


def test_install_claude_written_config_is_valid_json(tmp_path: Path) -> None:
    """Written claude_desktop_config.json must be parseable by json.loads."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())
    parsed = _json_module.loads(cfg.read_text())
    assert CLAUDE_TOP_LEVEL_KEY in parsed
    server = parsed[CLAUDE_TOP_LEVEL_KEY][CLAUDE_SERVER_NAME]
    assert server["type"] == CLAUDE_TRANSPORT
    assert server["url"] == CLAUDE_SSE_URL
    assert Path(server["url"].split("//")[1].split("/")[0]).parts  # URL is non-trivial


def test_install_claude_written_entry_uses_sse_url(tmp_path: Path) -> None:
    """Installed config entry must use an absolute SSE URL."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())
    parsed = _json_module.loads(cfg.read_text())
    entry = parsed[CLAUDE_TOP_LEVEL_KEY][CLAUDE_SERVER_NAME]
    assert entry["url"].startswith("http://"), (
        "Installed SSE URL must be an absolute HTTP URL"
    )
    assert CLAUDE_MCP_SSE_PATH in entry["url"], (
        "Installed URL must contain the MCP SSE path"
    )


def test_install_claude_preserves_unrelated_config(tmp_path: Path) -> None:
    """install_claude_config preserves unrelated JSON keys in existing config."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    existing = (
        _json_module.dumps({"globalShortcut": "Ctrl+I", "theme": "dark"}, indent=2)
        + "\n"
    )
    cfg.write_text(existing)
    install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())
    parsed = _json_module.loads(cfg.read_text())
    # Unrelated keys must survive
    assert parsed["globalShortcut"] == "Ctrl+I"
    assert parsed["theme"] == "dark"
    # New entry must be present
    assert CLAUDE_TOP_LEVEL_KEY in parsed
    assert CLAUDE_SERVER_NAME in parsed[CLAUDE_TOP_LEVEL_KEY]


def test_install_claude_preserves_other_mcp_servers(tmp_path: Path) -> None:
    """install_claude_config does not overwrite other mcpServers entries."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    existing = (
        _json_module.dumps(
            {
                "mcpServers": {
                    "other-server": {"type": "sse", "url": "http://localhost:9999/sse"}
                }
            },
            indent=2,
        )
        + "\n"
    )
    cfg.write_text(existing)
    install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())
    parsed = _json_module.loads(cfg.read_text())
    # Other server must still be present
    assert "other-server" in parsed[CLAUDE_TOP_LEVEL_KEY]
    # New server must also be present
    assert CLAUDE_SERVER_NAME in parsed[CLAUDE_TOP_LEVEL_KEY]


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def test_install_claude_creates_backup_when_file_exists(tmp_path: Path) -> None:
    """install_claude_config creates a timestamped backup of an existing config."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    original_content = _json_module.dumps({"theme": "dark"}, indent=2) + "\n"
    cfg.write_text(original_content)

    install_claude_config(
        yes=True,
        config_path=cfg,
        file=_io.StringIO(),
        _timestamp="20260419T103000",
    )

    backup = claude_dir / "claude_desktop_config.json.bak.20260419T103000"
    assert backup.is_file(), (
        "Backup file must be created when existing config is present"
    )
    assert backup.read_text() == original_content, (
        "Backup must contain the original content"
    )


def test_install_claude_backup_filename_uses_suffix_template(tmp_path: Path) -> None:
    """Backup filename matches CLAUDE_BACKUP_SUFFIX_TEMPLATE pattern."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    cfg.write_text(_json_module.dumps({"x": 1}) + "\n")
    install_claude_config(
        yes=True,
        config_path=cfg,
        file=_io.StringIO(),
        _timestamp="20260419T120000",
    )
    suffix = CLAUDE_BACKUP_SUFFIX_TEMPLATE.format(timestamp="20260419T120000")
    expected_backup = claude_dir / (CLAUDE_CONFIG_FILENAME + suffix)
    assert expected_backup.is_file()


def test_install_claude_no_backup_when_config_absent(tmp_path: Path) -> None:
    """No backup file is created when the config does not yet exist."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    install_claude_config(
        yes=True,
        config_path=cfg,
        file=_io.StringIO(),
        _timestamp="20260419T103000",
    )
    # Only the config file should exist, no backup
    files = list(claude_dir.iterdir())
    assert len(files) == 1
    assert files[0].name == CLAUDE_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_install_claude_is_idempotent(tmp_path: Path) -> None:
    """Running install twice returns 'already_installed' on the second run."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    r1 = install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())
    assert r1 == "installed"
    r2 = install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())
    assert r2 == "already_installed"


def test_install_claude_idempotent_no_backup_second_run(tmp_path: Path) -> None:
    """Idempotent re-run must not create an additional backup."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    install_claude_config(
        yes=True, config_path=cfg, file=_io.StringIO(), _timestamp="20260419T103000"
    )
    files_after_first = set(f.name for f in claude_dir.iterdir())
    install_claude_config(
        yes=True, config_path=cfg, file=_io.StringIO(), _timestamp="20260419T110000"
    )
    files_after_second = set(f.name for f in claude_dir.iterdir())
    assert files_after_second == files_after_first, (
        "Idempotent re-run must not add any new files"
    )


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------


def test_install_claude_yes_flag_skips_prompt(tmp_path: Path) -> None:
    """yes=True must install without calling input()."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    with _patch("builtins.input") as mock_input:
        install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())
    mock_input.assert_not_called()


def test_install_claude_confirmation_decline_returns_declined(tmp_path: Path) -> None:
    """Responding 'n' to the confirmation prompt returns 'declined'."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    with _patch("builtins.input", return_value="n"):
        result = install_claude_config(config_path=cfg, file=_io.StringIO())
    assert result == "declined"
    assert not cfg.exists(), "Declined install must not write any files"


def test_install_claude_confirmation_accept_returns_installed(tmp_path: Path) -> None:
    """Responding 'y' to the confirmation prompt returns 'installed'."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    with _patch("builtins.input", return_value="y"):
        result = install_claude_config(config_path=cfg, file=_io.StringIO())
    assert result == "installed"
    assert cfg.is_file()


def test_install_claude_confirmation_eof_returns_declined(tmp_path: Path) -> None:
    """EOFError on input (non-interactive) treats as 'n' and returns 'declined'."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    with _patch("builtins.input", side_effect=EOFError):
        result = install_claude_config(config_path=cfg, file=_io.StringIO())
    assert result == "declined"


# ---------------------------------------------------------------------------
# Refusal conditions
# ---------------------------------------------------------------------------


def test_install_claude_refuses_when_config_dir_missing(tmp_path: Path) -> None:
    """install_claude_config raises ClaudeInstallError if config dir doesn't exist."""
    cfg = tmp_path / "nonexistent" / "claude_desktop_config.json"
    with pytest.raises(ClaudeInstallError, match="does not exist"):
        install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())


def test_install_claude_refuses_symlink_config(tmp_path: Path) -> None:
    """install_claude_config raises ClaudeInstallError if config file is a symlink."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    target = tmp_path / "real_config.json"
    target.write_text("{}")
    cfg.symlink_to(target)
    with pytest.raises(ClaudeInstallError, match="symlink"):
        install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())


def test_install_claude_refuses_non_regular_file(tmp_path: Path) -> None:
    """install_claude_config raises ClaudeInstallError if config path is a directory."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    cfg.mkdir()
    with pytest.raises(ClaudeInstallError, match="regular file"):
        install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())


def test_install_claude_refuses_unparseable_json(tmp_path: Path) -> None:
    """install_claude_config raises ClaudeInstallError if existing JSON is malformed."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    cfg.write_text("this is {not valid json [\n")
    with pytest.raises(ClaudeInstallError, match=r"[Pp]ars"):
        install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())


def test_install_claude_refuses_conflicting_entry(tmp_path: Path) -> None:
    """install_claude_config raises ClaudeInstallError for conflicting entry."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    # Write a mcpServers.modal-mcp with a different url
    cfg.write_text(
        _json_module.dumps(
            {
                "mcpServers": {
                    "modal-mcp": {"type": "sse", "url": "http://wrong:9999/mcp/sse"}
                }
            },
            indent=2,
        )
        + "\n"
    )
    with pytest.raises(ClaudeInstallError, match="incompatible"):
        install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())


def test_install_claude_refuses_mcp_servers_non_dict(tmp_path: Path) -> None:
    """install_claude_config raises ClaudeInstallError if mcpServers is not a dict."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    cfg.write_text(_json_module.dumps({"mcpServers": "not an object"}) + "\n")
    with pytest.raises(ClaudeInstallError, match=r"[Oo]bject|object"):
        install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())


def test_install_claude_raises_on_unsupported_platform(tmp_path: Path) -> None:
    """install_claude_config raises ClaudeInstallError on unsupported platforms."""
    with (
        patch.object(sys, "platform", "freebsd14"),
        pytest.raises(ClaudeInstallError, match=r"[Uu]nsupported platform"),
    ):
        install_claude_config(yes=True, file=_io.StringIO())


# ---------------------------------------------------------------------------
# Validation failure — filesystem recovery
# ---------------------------------------------------------------------------


def test_install_claude_validation_failure_removes_file_when_no_backup(
    tmp_path: Path,
) -> None:
    """Validation failure on fresh install removes the written file.

    When no prior config existed (no backup was created), a post-write
    validation failure must unlink the freshly-written file so that the
    filesystem is returned to its pre-install state.
    """
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    assert not cfg.exists(), "Pre-condition: config must not exist before install"

    # Inject a validation error by making _json.loads raise on every call.
    with (
        _patch(
            "modal_mcp.agent_targets.claude.json.loads",
            side_effect=_json_module.JSONDecodeError("injected", "", 0),
        ),
        pytest.raises(ClaudeInstallError, match=r"[Vv]alidation"),
    ):
        install_claude_config(yes=True, config_path=cfg, file=_io.StringIO())

    # The freshly-written (now invalid) file must have been removed.
    assert not cfg.exists(), (
        "Validation failure with no prior config must remove the written file; "
        f"{cfg} still exists on disk"
    )


def test_install_claude_validation_failure_with_backup_restores_original(
    tmp_path: Path,
) -> None:
    """Validation failure with an existing config restores the backup.

    When a prior config existed (backup was created), a post-write validation
    failure must restore the original content from the backup.
    """
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    original_content = _json_module.dumps({"theme": "dark"}, indent=2) + "\n"
    cfg.write_text(original_content)

    with (
        _patch(
            "modal_mcp.agent_targets.claude.json.loads",
            side_effect=_json_module.JSONDecodeError("injected", "", 0),
        ),
        pytest.raises(ClaudeInstallError, match=r"[Vv]alidation"),
    ):
        install_claude_config(
            yes=True,
            config_path=cfg,
            file=_io.StringIO(),
            _timestamp="20260420T000000",
        )

    # Config must still exist and contain the original content.
    assert cfg.exists(), "Config must still exist after backup restore"
    assert cfg.read_text() == original_content, (
        "Config must contain the original content after backup restore"
    )


# ---------------------------------------------------------------------------
# Custom bind address
# ---------------------------------------------------------------------------


def test_install_claude_custom_bind_is_registered(tmp_path: Path) -> None:
    """install_claude_config embeds a custom bind address in the written config."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    custom_bind = "127.0.0.1:9876"
    install_claude_config(
        bind=custom_bind, yes=True, config_path=cfg, file=_io.StringIO()
    )
    parsed = _json_module.loads(cfg.read_text())
    entry = parsed[CLAUDE_TOP_LEVEL_KEY][CLAUDE_SERVER_NAME]
    assert custom_bind in entry["url"], (
        f"Installed URL must contain custom bind {custom_bind!r}; got {entry['url']!r}"
    )


def test_install_claude_custom_bind_idempotent(tmp_path: Path) -> None:
    """Idempotency check uses the custom URL for comparison."""
    claude_dir = _make_claude_dir(tmp_path)
    cfg = _claude_config(claude_dir)
    custom_bind = "127.0.0.1:9876"
    install_claude_config(
        bind=custom_bind, yes=True, config_path=cfg, file=_io.StringIO()
    )
    r2 = install_claude_config(
        bind=custom_bind, yes=True, config_path=cfg, file=_io.StringIO()
    )
    assert r2 == "already_installed"
