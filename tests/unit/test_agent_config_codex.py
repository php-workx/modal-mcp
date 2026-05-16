"""Unit tests for the Codex agent target contract and print support.

These tests assert the structured :data:`CODEX_CONTRACT` fields *before* any
install code is written, ensuring the contract is complete and self-consistent.
Each acceptance-criteria item from ticket mm-e7e1 is covered by at least one
test function.

Print-support tests (ticket mm-ss6f) verify:
- ``format_config_snippet()`` produces valid, absolute-path TOML.
- ``print_agent_config("codex")`` emits a complete snippet without writing
  files or leaking secrets.

Contract summary being verified:
- Config path: ~/.codex/config.toml (TOML format)
- Transport: stdio (command + args subprocess launch)
- Top-level key: mcp_servers (snake_case)
- Server name: modal-mcp
- Env-file strategy: absolute_path_flag (--env-file in args)
- Refusal conditions: >= 8 covering filesystem-safety cases
"""

from __future__ import annotations

import dataclasses
import io
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from modal_mcp.agent_targets import get_target
from modal_mcp.agent_targets.codex import (
    CODEX_AGENT_NAME,
    CODEX_BACKUP_SUFFIX_TEMPLATE,
    CODEX_CONFIG_FILENAME,
    CODEX_CONFIG_FORMAT,
    CODEX_CONTRACT,
    CODEX_IDEMPOTENCY_KEY,
    CODEX_SERVER_ARGS_TEMPLATE,
    CODEX_SERVER_COMMAND,
    CODEX_SERVER_NAME,
    CODEX_TOP_LEVEL_KEY,
    CODEX_TRANSPORT,
    AgentTargetContract,
    CodexInstallError,
    build_contract,
    format_config_snippet,
    install as install_codex_config,
)


def print_agent_config(target: str, **kwargs) -> None:
    """Test helper mirroring the old agent_config.print_agent_config surface."""
    get_target(target).render(**kwargs)

# ---------------------------------------------------------------------------
# Field presence and type
# ---------------------------------------------------------------------------


def test_codex_contract_is_agent_target_contract() -> None:
    """CODEX_CONTRACT is an instance of AgentTargetContract."""
    assert isinstance(CODEX_CONTRACT, AgentTargetContract)


def test_codex_contract_declares_required_fields() -> None:
    """All contract fields are populated with non-empty, non-None values.

    This is the primary gate: no placeholder or blank value is allowed.
    Optional fields (server_url) are allowed to be None only when their
    corresponding transport type makes them inapplicable.
    """
    c = CODEX_CONTRACT

    # String fields that must always be non-empty
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

    # refusal_conditions tuple must have at least eight entries
    assert len(c.refusal_conditions) >= 8, (
        f"at least eight refusal conditions must be declared, "
        f"got {len(c.refusal_conditions)}"
    )
    assert all(cond for cond in c.refusal_conditions), (
        "each refusal condition must be a non-empty string"
    )


# ---------------------------------------------------------------------------
# Config location
# ---------------------------------------------------------------------------


def test_codex_contract_representative_config_path() -> None:
    """Codex target is the canonical user-level TOML config file."""
    assert CODEX_CONTRACT.representative_config_path == Path("~/.codex/config.toml")


def test_codex_contract_representative_config_path_filename() -> None:
    """representative_config_path.name must equal CODEX_CONFIG_FILENAME."""
    assert CODEX_CONTRACT.representative_config_path.name == CODEX_CONFIG_FILENAME
    assert CODEX_CONTRACT.representative_config_path.name == "config.toml"


def test_codex_contract_has_no_target_config_path_field() -> None:
    """AgentTargetContract must not expose a 'target_config_path' field.

    The field was renamed to 'representative_config_path' to make the
    semantics explicit.  Install code should call the appropriate path
    resolver rather than reading this field directly.
    """
    assert not hasattr(CODEX_CONTRACT, "target_config_path"), (
        "AgentTargetContract must not have a 'target_config_path' attribute; "
        "use 'representative_config_path' instead."
    )


def test_codex_contract_config_format_is_toml() -> None:
    """Codex config format is TOML, not YAML or JSON."""
    assert CODEX_CONTRACT.config_format == "toml"
    assert CODEX_CONTRACT.config_format == CODEX_CONFIG_FORMAT


# ---------------------------------------------------------------------------
# MCP transport
# ---------------------------------------------------------------------------


def test_codex_contract_mcp_transport_is_stdio() -> None:
    """Codex uses stdio transport; the CLI launches modal-mcp as a subprocess."""
    assert CODEX_CONTRACT.mcp_transport == "stdio"
    assert CODEX_CONTRACT.mcp_transport == CODEX_TRANSPORT


def test_codex_contract_server_name() -> None:
    """Server name is the key used within mcp_servers."""
    assert CODEX_CONTRACT.server_name == "modal-mcp"
    assert CODEX_CONTRACT.server_name == CODEX_SERVER_NAME


def test_codex_contract_top_level_key() -> None:
    """MCP servers are registered under the mcp_servers top-level key (snake_case)."""
    assert CODEX_CONTRACT.top_level_key == "mcp_servers"
    assert CODEX_CONTRACT.top_level_key == CODEX_TOP_LEVEL_KEY


# ---------------------------------------------------------------------------
# stdio transport consistency
# ---------------------------------------------------------------------------


def test_codex_print_config_contains_command_and_no_url() -> None:
    """Contract declares a concrete stdio command and no HTTP URL.

    The install/print code can produce a complete config snippet from the
    contract fields alone, with no further research required.
    """
    # stdio transport: command must be set
    assert CODEX_CONTRACT.server_command is not None
    assert CODEX_CONTRACT.server_command == "modal-mcp"
    assert CODEX_CONTRACT.server_command == CODEX_SERVER_COMMAND
    # stdio transport: no URL
    assert CODEX_CONTRACT.server_url is None


def test_codex_contract_server_args_template_contains_env_file_flag() -> None:
    """server_args_template must contain the --env-file flag placeholder."""
    args = CODEX_CONTRACT.server_args_template
    assert "--env-file" in args, "args template must include --env-file flag"
    assert "{env_file}" in args, "args template must include {env_file} placeholder"
    assert args == CODEX_SERVER_ARGS_TEMPLATE


def test_codex_contract_server_args_template_starts_with_stdio() -> None:
    """server_args_template must begin with 'stdio' subcommand."""
    args = CODEX_CONTRACT.server_args_template
    assert args[0] == "stdio", "first arg must be the 'stdio' subcommand"


def test_codex_command_snippet_uses_absolute_env_file() -> None:
    """stdio transport: env-file is injected into the Codex config via --env-file.

    Codex launches modal-mcp as a subprocess; it must be given the env-file
    path via the args list.  Therefore env_file_strategy is 'absolute_path_flag'.
    """
    assert CODEX_CONTRACT.env_file_strategy == "absolute_path_flag"
    assert not CODEX_CONTRACT.supports_cwd_config


# ---------------------------------------------------------------------------
# Merge key and idempotency
# ---------------------------------------------------------------------------


def test_codex_contract_idempotency_key() -> None:
    """Idempotency key encodes the full dotted path to the server entry."""
    assert CODEX_CONTRACT.idempotency_key == "mcp_servers.modal-mcp"
    assert CODEX_CONTRACT.idempotency_key == CODEX_IDEMPOTENCY_KEY


def test_codex_contract_merge_key_components_are_consistent() -> None:
    """Idempotency key is composed of top_level_key and server_name."""
    parts = CODEX_CONTRACT.idempotency_key.split(".")
    assert len(parts) == 2
    assert parts[0] == CODEX_CONTRACT.top_level_key
    assert parts[1] == CODEX_CONTRACT.server_name


# ---------------------------------------------------------------------------
# Backup path
# ---------------------------------------------------------------------------


def test_codex_contract_backup_suffix_has_timestamp_placeholder() -> None:
    """Backup suffix template contains a {timestamp} placeholder."""
    assert "{timestamp}" in CODEX_CONTRACT.backup_suffix_template
    assert "{timestamp}" in CODEX_BACKUP_SUFFIX_TEMPLATE


def test_codex_contract_backup_suffix_references_config_extension() -> None:
    """Backup suffix starts with a dot, producing a hidden backup file."""
    assert CODEX_CONTRACT.backup_suffix_template.startswith(".")


def test_codex_backup_suffix_template_produces_filesystem_safe_name() -> None:
    """The substituted backup filename must not contain characters illegal on Windows.

    Windows NTFS/FAT32 forbid: ``\\ / : * ? " < > |``.
    The compact timestamp format (``YYYYMMDDTHHmmss``) satisfies this.
    """
    timestamp = "20260419T103000"
    backup_name = CODEX_CONFIG_FILENAME + CODEX_BACKUP_SUFFIX_TEMPLATE.format(
        timestamp=timestamp
    )
    _WINDOWS_ILLEGAL = set('\\/:*?"<>|')
    illegal_found = _WINDOWS_ILLEGAL & set(backup_name)
    assert not illegal_found, (
        f"Backup filename {backup_name!r} contains Windows-illegal characters: "
        f"{illegal_found!r}.  Use compact timestamp without ':' (e.g. "
        f"'20260419T103000' instead of '2026-04-19T10:30:00')."
    )


# ---------------------------------------------------------------------------
# Refusal conditions
# ---------------------------------------------------------------------------


def test_codex_contract_refusal_conditions_cover_missing_config_dir() -> None:
    """At least one refusal condition addresses a missing config directory."""
    joined = " ".join(CODEX_CONTRACT.refusal_conditions).lower()
    assert "directory" in joined or "not installed" in joined or "not found" in joined


def test_codex_contract_refusal_conditions_cover_unparseable_toml() -> None:
    """At least one refusal condition addresses TOML parse failure."""
    joined = " ".join(CODEX_CONTRACT.refusal_conditions).lower()
    assert "toml" in joined or "parse" in joined


def test_codex_contract_refusal_conditions_cover_non_mapping() -> None:
    """At least one refusal condition addresses unexpected top-level type."""
    joined = " ".join(CODEX_CONTRACT.refusal_conditions).lower()
    assert "mapping" in joined or "dict" in joined or "table" in joined


def test_codex_contract_refusal_conditions_cover_conflicting_entry() -> None:
    """At least one refusal condition addresses a pre-existing conflicting entry."""
    joined = " ".join(CODEX_CONTRACT.refusal_conditions).lower()
    assert "conflict" in joined or "exist" in joined or "incompatible" in joined


def test_codex_contract_refusal_conditions_cover_symlink() -> None:
    """At least one refusal condition addresses symlink config targets.

    Writing through a symlink could overwrite an unintended file; the
    installer must refuse rather than follow the symlink.
    """
    joined = " ".join(CODEX_CONTRACT.refusal_conditions).lower()
    assert "symlink" in joined, (
        "CODEX_CONTRACT must include a refusal condition for symlink config paths"
    )


def test_codex_contract_refusal_conditions_cover_non_regular_file() -> None:
    """At least one refusal condition addresses non-regular-file config targets.

    If the config path is a directory, FIFO, socket, or device node the write
    must be refused rather than attempted.
    """
    joined = " ".join(CODEX_CONTRACT.refusal_conditions).lower()
    assert (
        "directory" in joined
        or "fifo" in joined
        or "socket" in joined
        or "regular file" in joined
        or "device" in joined
    ), (
        "CODEX_CONTRACT must include a refusal condition for non-regular-file "
        "config targets (directory, FIFO, socket, device node)"
    )


def test_codex_contract_refusal_conditions_cover_permission_denied() -> None:
    """At least one refusal condition addresses permission-denied errors.

    On macOS/Linux, the config file may be owned by a different UID after a
    user switch.  The installer must refuse rather than crash.
    """
    joined = " ".join(CODEX_CONTRACT.refusal_conditions).lower()
    assert "permission" in joined or "read" in joined or "writable" in joined, (
        "CODEX_CONTRACT must include a refusal condition for permission-denied "
        "on read or write"
    )


def test_codex_contract_refusal_conditions_cover_disk_full() -> None:
    """At least one refusal condition addresses disk-full / ENOSPC errors.

    The installer performs an atomic write via a temp file; if the disk is
    full the write will fail at the OS level.
    """
    joined = " ".join(CODEX_CONTRACT.refusal_conditions).lower()
    assert "disk" in joined or "enospc" in joined or "space" in joined, (
        "CODEX_CONTRACT must include a refusal condition for disk full / ENOSPC"
    )


def test_codex_contract_has_at_least_eight_refusal_conditions() -> None:
    """refusal_conditions must cover all declared failure modes (at least eight).

    Required coverage: (1) missing config dir, (2) symlink, (3) unparseable
    TOML, (4) non-table top-level, (5) permission denied, (6) disk full,
    (7) non-regular file, (8) conflicting entry.
    """
    assert len(CODEX_CONTRACT.refusal_conditions) >= 8, (
        f"Expected >= 8 refusal conditions, got "
        f"{len(CODEX_CONTRACT.refusal_conditions)}"
    )


def test_codex_install_refuses_unparseable_config() -> None:
    """Contract declares that unparseable config must abort the install.

    This test verifies the *contract* declares the refusal condition; the
    actual install guard is implemented in a later ticket.
    """
    unparseable_condition = next(
        (
            c
            for c in CODEX_CONTRACT.refusal_conditions
            if "parse" in c.lower() or "toml" in c.lower()
        ),
        None,
    )
    assert unparseable_condition is not None, (
        "CODEX_CONTRACT must list a refusal condition for unparseable config"
    )


# ---------------------------------------------------------------------------
# Parse validation strategy
# ---------------------------------------------------------------------------


def test_codex_contract_parse_validation_strategy_references_toml() -> None:
    """Parse validation strategy mentions the TOML round-trip check."""
    strategy = CODEX_CONTRACT.parse_validation_strategy.lower()
    assert "toml" in strategy


def test_codex_contract_parse_validation_strategy_mentions_expected_values() -> None:
    """Parse validation strategy requires the written entry to be verified."""
    strategy = CODEX_CONTRACT.parse_validation_strategy.lower()
    # Must verify the entry is present after writing
    assert "modal-mcp" in strategy or "mcp_servers" in strategy
    # Must restore on failure
    assert "backup" in strategy or "restore" in strategy or "fail" in strategy


def test_codex_contract_parse_validation_strategy_mentions_command() -> None:
    """Parse validation strategy embeds the server command for verification."""
    strategy = CODEX_CONTRACT.parse_validation_strategy
    assert CODEX_SERVER_COMMAND in strategy


# ---------------------------------------------------------------------------
# Dry-run description
# ---------------------------------------------------------------------------


def test_codex_install_dry_run_prints_target_and_change() -> None:
    """Dry-run description references the target file and the change.

    The install code produces dry-run output from this description; this test
    verifies the contract description is informative enough to use directly.
    """
    desc = CODEX_CONTRACT.dry_run_description
    # Must mention the target file
    assert "config.toml" in desc
    # Must describe the command-based entry
    assert "modal-mcp" in desc
    # Must NOT claim to use HTTP (that would be the wrong transport)
    assert "http" not in desc.lower()


# ---------------------------------------------------------------------------
# Install contract sufficiency
# ---------------------------------------------------------------------------


def test_codex_contract_is_sufficient_to_implement_install() -> None:
    """All fields required to implement install without further research are set.

    This is a high-level gate: if any new required field is added to
    AgentTargetContract its absence here will cause a dataclasses error, and
    this test ensures the values are substantive.
    """
    c = CODEX_CONTRACT

    # Transport is fully specified (server_command must be set for stdio)
    assert c.server_command is not None, (
        "server_command must be set for stdio transport"
    )
    assert c.server_url is None, "server_url must be None for stdio transport"

    # Args template has the env-file placeholder
    assert "{env_file}" in c.server_args_template, (
        "server_args_template must contain {env_file} placeholder"
    )

    # Env-file strategy is declared
    assert c.env_file_strategy == "absolute_path_flag"

    # Merge path is unambiguous
    assert c.top_level_key and c.server_name and c.idempotency_key

    # Backup can be computed
    assert "{timestamp}" in c.backup_suffix_template

    # Refusal conditions are comprehensive
    assert len(c.refusal_conditions) >= 8

    # Validation strategy is actionable
    assert len(c.parse_validation_strategy) >= 20

    # Config location and format are explicit
    assert c.config_format == "toml"
    assert c.representative_config_path != Path()


def test_codex_install_creates_backup_and_preserves_unrelated_config() -> None:
    """Contract declares backup behaviour and refusal for conflicting entries.

    The actual backup creation is in a later install ticket; this test
    confirms the contract specifies the required backup suffix template and
    that the refusal conditions protect unrelated config from corruption.
    """
    # Backup mechanism is declared
    assert CODEX_CONTRACT.backup_suffix_template
    assert "{timestamp}" in CODEX_CONTRACT.backup_suffix_template

    # A conflicting-entry refusal condition protects unrelated config
    conflict_condition = next(
        (
            c
            for c in CODEX_CONTRACT.refusal_conditions
            if "conflict" in c.lower()
            or "exist" in c.lower()
            or "incompatible" in c.lower()
        ),
        None,
    )
    assert conflict_condition is not None, (
        "CODEX_CONTRACT must list a refusal condition for conflicting entries"
    )


def test_codex_install_is_idempotent() -> None:
    """Contract declares an idempotency key for detecting existing registrations.

    The actual idempotency guard is in a later install ticket; this test
    confirms the key is set so reruns can be made safe.
    """
    assert CODEX_CONTRACT.idempotency_key == "mcp_servers.modal-mcp"


# ---------------------------------------------------------------------------
# TOML round-trip: generated block is structurally valid TOML
# ---------------------------------------------------------------------------


def test_codex_contract_generated_block_is_valid_toml() -> None:
    """The rendered config block must be parseable by tomllib.

    This test substitutes a concrete env_file path into the args template and
    verifies that the resulting TOML snippet is structurally valid.  It proves
    that install code producing this shape won't corrupt the config with
    invalid TOML syntax.
    """
    env_file = "/home/user/project/.env"
    rendered_args = [
        arg.format(env_file=env_file) if "{env_file}" in arg else arg
        for arg in CODEX_CONTRACT.server_args_template
    ]
    # Build a minimal TOML string matching the documented generated block
    args_toml = ", ".join(f'"{a}"' for a in rendered_args)
    toml_block = (
        f"[{CODEX_CONTRACT.top_level_key}.{CODEX_CONTRACT.server_name}]\n"
        f'command = "{CODEX_CONTRACT.server_command}"\n'
        f"args = [{args_toml}]\n"
    )
    parsed = tomllib.loads(toml_block)
    server_table = parsed[CODEX_CONTRACT.top_level_key][CODEX_CONTRACT.server_name]
    assert server_table["command"] == CODEX_CONTRACT.server_command
    assert server_table["args"][0] == "stdio"
    assert "--env-file" in server_table["args"]
    assert env_file in server_table["args"]


def test_codex_contract_generated_block_has_correct_toml_key_shape() -> None:
    """The generated TOML key uses snake_case (mcp_servers), not camelCase.

    The Codex CLI config schema requires snake_case for the MCP servers key.
    camelCase (mcpServers) would be silently ignored by the CLI.
    """
    toml_key = f"[{CODEX_CONTRACT.top_level_key}.{CODEX_CONTRACT.server_name}]"
    assert "mcp_servers" in toml_key, (
        f"Expected snake_case 'mcp_servers' in TOML key, got: {toml_key!r}"
    )
    assert "mcpServers" not in toml_key, (
        f"camelCase 'mcpServers' must not appear in TOML key: {toml_key!r}"
    )


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_codex_contract_is_frozen() -> None:
    """AgentTargetContract instances are immutable (frozen dataclass)."""
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        CODEX_CONTRACT.agent_name = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_contract()
# ---------------------------------------------------------------------------


def test_build_contract_returns_contract_instance() -> None:
    """build_contract() must return an AgentTargetContract."""
    contract = build_contract()
    assert isinstance(contract, AgentTargetContract)


def test_build_contract_default_command_equals_constant() -> None:
    """build_contract() with no args must use CODEX_SERVER_COMMAND."""
    contract = build_contract()
    assert contract.server_command == CODEX_SERVER_COMMAND


def test_build_contract_custom_command_updates_server_command() -> None:
    """build_contract() with a custom command must update server_command."""
    custom_cmd = "/home/user/.local/bin/modal-mcp"
    contract = build_contract(custom_cmd)
    assert contract.server_command == custom_cmd


def test_build_contract_custom_command_updates_all_command_bearing_fields() -> None:
    """build_contract() must propagate custom command to ALL fields that embed it.

    Install code MUST call build_contract(command=resolved_cmd) rather than
    using the module-level CODEX_CONTRACT singleton when modal-mcp is not on
    PATH.  Otherwise parse_validation_strategy and dry_run_description would
    reference a command that Codex cannot find.
    """
    custom_cmd = "/opt/modal-mcp/bin/modal-mcp"
    contract = build_contract(custom_cmd)

    assert contract.server_command == custom_cmd, (
        "server_command must reflect the custom command"
    )
    assert custom_cmd in contract.parse_validation_strategy, (
        "parse_validation_strategy must embed the runtime command so that "
        "install code can use it directly"
    )
    assert custom_cmd in contract.dry_run_description, (
        "dry_run_description must embed the runtime command so that "
        "dry-run output shows the actual registered command"
    )


def test_build_contract_immutable() -> None:
    """Returned AgentTargetContract must be immutable (frozen dataclass)."""
    contract = build_contract()
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        contract.mcp_transport = "http"  # type: ignore[misc]


def test_install_code_must_call_build_contract_not_use_singleton() -> None:
    """Contractual requirement: install code MUST call
    build_contract(command=resolved_cmd) when modal-mcp is not on PATH.

    CODEX_CONTRACT is frozen with CODEX_SERVER_COMMAND.  If a user has
    modal-mcp installed at /opt/modal/bin/modal-mcp and install code imports
    CODEX_CONTRACT instead of calling build_contract(command=resolved_cmd),
    the registered command will be 'modal-mcp' — a command Codex cannot find.
    This test explicitly documents and enforces the requirement.
    """
    custom_cmd = "/opt/modal/bin/modal-mcp"
    install_contract = build_contract(command=custom_cmd)

    # The install contract's command must differ from the singleton's command
    assert install_contract.server_command != CODEX_CONTRACT.server_command, (
        "build_contract(command=custom) must produce a different server_command "
        "than the CODEX_CONTRACT singleton"
    )

    # All command-bearing fields must reflect the custom command
    assert install_contract.server_command == custom_cmd
    assert custom_cmd in install_contract.parse_validation_strategy
    assert custom_cmd in install_contract.dry_run_description
    # The singleton must still carry the default command (not contaminated)
    assert CODEX_CONTRACT.server_command == CODEX_SERVER_COMMAND


# ---------------------------------------------------------------------------
# Module-level constant consistency
# ---------------------------------------------------------------------------


def test_codex_contract_constants_match_contract_fields() -> None:
    """Module-level constants must equal the corresponding contract fields."""
    c = CODEX_CONTRACT
    assert c.agent_name == CODEX_AGENT_NAME
    assert c.config_format == CODEX_CONFIG_FORMAT
    assert c.mcp_transport == CODEX_TRANSPORT
    assert c.server_name == CODEX_SERVER_NAME
    assert c.top_level_key == CODEX_TOP_LEVEL_KEY
    assert c.idempotency_key == CODEX_IDEMPOTENCY_KEY
    assert c.server_command == CODEX_SERVER_COMMAND
    assert c.server_args_template == CODEX_SERVER_ARGS_TEMPLATE
    assert c.backup_suffix_template == CODEX_BACKUP_SUFFIX_TEMPLATE


# ===========================================================================
# format_config_snippet()
# ===========================================================================


def test_format_config_snippet_returns_string() -> None:
    """format_config_snippet() must return a non-empty string."""
    snippet = format_config_snippet()
    assert isinstance(snippet, str)
    assert snippet


def test_format_config_snippet_default_is_valid_toml() -> None:
    """format_config_snippet() with no args must produce parseable TOML."""
    snippet = format_config_snippet()
    parsed = tomllib.loads(snippet)
    assert CODEX_TOP_LEVEL_KEY in parsed
    assert CODEX_SERVER_NAME in parsed[CODEX_TOP_LEVEL_KEY]


def test_format_config_snippet_contains_command_field() -> None:
    """Snippet must include command = '<modal-mcp>'."""
    snippet = format_config_snippet()
    assert f'command = "{CODEX_SERVER_COMMAND}"' in snippet


def test_format_config_snippet_contains_env_file_flag() -> None:
    """Snippet must include the --env-file flag in args."""
    snippet = format_config_snippet()
    assert "--env-file" in snippet


def test_format_config_snippet_default_env_file_is_absolute() -> None:
    """Default placeholder in format_config_snippet() must be absolute-looking."""
    snippet = format_config_snippet()
    parsed = tomllib.loads(snippet)
    args = parsed[CODEX_TOP_LEVEL_KEY][CODEX_SERVER_NAME]["args"]
    env_file_idx = args.index("--env-file")
    env_path = args[env_file_idx + 1]
    assert Path(env_path).is_absolute() or env_path.startswith("/"), (
        f"Default env-file placeholder must be an absolute path; got: {env_path!r}"
    )


def test_format_config_snippet_with_absolute_env_file(tmp_path: Path) -> None:
    """Supplying an absolute env_file must embed that path in the snippet."""
    abs_path = str(tmp_path / ".env")
    snippet = format_config_snippet(env_file=abs_path)
    assert abs_path in snippet
    parsed = tomllib.loads(snippet)
    args = parsed[CODEX_TOP_LEVEL_KEY][CODEX_SERVER_NAME]["args"]
    assert abs_path in args


def test_format_config_snippet_with_path_object(tmp_path: Path) -> None:
    """format_config_snippet() must accept a pathlib.Path for env_file."""
    abs_path = tmp_path / ".env"
    snippet = format_config_snippet(env_file=abs_path)
    assert str(abs_path) in snippet


def test_format_config_snippet_rejects_relative_env_file() -> None:
    """format_config_snippet() must raise ValueError for a relative env_file."""
    with pytest.raises(ValueError, match="absolute path"):
        format_config_snippet(env_file="relative/.env")


def test_format_config_snippet_custom_command() -> None:
    """format_config_snippet() must embed the custom command."""
    custom_cmd = "/opt/modal/bin/modal-mcp"
    snippet = format_config_snippet(command=custom_cmd)
    assert f'command = "{custom_cmd}"' in snippet
    parsed = tomllib.loads(snippet)
    assert parsed[CODEX_TOP_LEVEL_KEY][CODEX_SERVER_NAME]["command"] == custom_cmd


def test_format_config_snippet_args_start_with_stdio() -> None:
    """The rendered args list must begin with the 'stdio' subcommand."""
    snippet = format_config_snippet()
    parsed = tomllib.loads(snippet)
    args = parsed[CODEX_TOP_LEVEL_KEY][CODEX_SERVER_NAME]["args"]
    assert args[0] == "stdio"


def test_format_config_snippet_uses_correct_toml_key() -> None:
    """Snippet must use [mcp_servers.modal-mcp] (snake_case, not camelCase)."""
    snippet = format_config_snippet()
    assert "[mcp_servers.modal-mcp]" in snippet
    assert "mcpServers" not in snippet


def test_format_config_snippet_does_not_contain_secrets() -> None:
    """Snippet must not contain secrets or credentials."""
    snippet = format_config_snippet(env_file="/abs/.env")
    # The snippet should only contain structural config, not actual .env content
    _secret_patterns = ("password", "token", "api_key", "secret", "credential")
    snippet_lower = snippet.lower()
    for pattern in _secret_patterns:
        assert pattern not in snippet_lower, (
            f"Snippet must not contain secret-related keyword: {pattern!r}"
        )


# ===========================================================================
# print_agent_config() — Codex target
# ===========================================================================


def test_print_agent_config_codex_returns_none() -> None:
    """print_agent_config('codex') must return None (side-effect only)."""
    buf = io.StringIO()
    result = print_agent_config("codex", file=buf)
    assert result is None


def test_print_agent_config_codex_writes_to_file_arg() -> None:
    """print_agent_config('codex') must write to the supplied file argument."""
    buf = io.StringIO()
    print_agent_config("codex", file=buf)
    output = buf.getvalue()
    assert output, "Output must be non-empty"


def test_print_agent_config_codex_output_contains_toml_section() -> None:
    """print_agent_config('codex') must include the [mcp_servers.modal-mcp] section."""
    buf = io.StringIO()
    print_agent_config("codex", file=buf)
    output = buf.getvalue()
    assert "[mcp_servers.modal-mcp]" in output


def test_print_agent_config_codex_output_contains_command() -> None:
    """print_agent_config('codex') must include the modal-mcp command."""
    buf = io.StringIO()
    print_agent_config("codex", file=buf)
    output = buf.getvalue()
    assert "modal-mcp" in output
    assert "command" in output


def test_print_agent_config_codex_output_contains_env_file_flag() -> None:
    """print_agent_config('codex') must include the --env-file flag."""
    buf = io.StringIO()
    print_agent_config("codex", file=buf)
    output = buf.getvalue()
    assert "--env-file" in output


def test_print_agent_config_codex_default_env_file_path_is_absolute() -> None:
    """Default env-file path in print output must be absolute (starts with '/')."""
    buf = io.StringIO()
    print_agent_config("codex", file=buf)
    output = buf.getvalue()
    # Extract the args line and find the path after --env-file
    lines = output.splitlines()
    args_line = next((ln for ln in lines if "args" in ln and "--env-file" in ln), None)
    assert args_line is not None, "Output must contain the args line with --env-file"
    # The path after --env-file must be absolute (starts with '/')
    idx = args_line.find("--env-file")
    after_flag = args_line[idx + len("--env-file") :].strip()
    # Expect something like: ", "/path/..."]
    # The next quoted string is the path
    import re

    paths = re.findall(r'"(/[^"]+)"', after_flag)
    assert paths, f"Could not find an absolute path after --env-file in: {args_line!r}"
    assert paths[0].startswith("/"), (
        f"Path after --env-file must be absolute; got: {paths[0]!r}"
    )


def test_print_agent_config_codex_with_env_file() -> None:
    """print_agent_config('codex', env_file=...) must embed the given path."""
    abs_env = "/home/user/project/.env"
    buf = io.StringIO()
    print_agent_config("codex", env_file=abs_env, file=buf)
    output = buf.getvalue()
    assert abs_env in output


def test_print_agent_config_codex_with_path_object_env_file() -> None:
    """print_agent_config('codex', env_file=Path(...)) must accept Path objects."""
    abs_env = Path("/home/user/project/.env")
    buf = io.StringIO()
    print_agent_config("codex", env_file=abs_env, file=buf)
    output = buf.getvalue()
    assert str(abs_env) in output


def test_print_agent_config_codex_rejects_relative_env_file() -> None:
    """print_agent_config('codex') must raise ValueError for relative env_file."""
    with pytest.raises(ValueError, match="absolute path"):
        print_agent_config("codex", env_file="relative/.env", file=io.StringIO())


def test_print_agent_config_codex_with_custom_command() -> None:
    """print_agent_config('codex', command=...) must embed the custom command."""
    custom_cmd = "/opt/modal/bin/modal-mcp"
    buf = io.StringIO()
    print_agent_config("codex", command=custom_cmd, file=buf)
    output = buf.getvalue()
    assert custom_cmd in output


def test_print_agent_config_codex_output_is_valid_toml() -> None:
    """The TOML section in print_agent_config('codex') output must be parseable."""
    buf = io.StringIO()
    print_agent_config("codex", file=buf)
    output = buf.getvalue()
    # Extract only the lines that form the TOML block (skip comment lines)
    toml_lines = [ln for ln in output.splitlines() if not ln.startswith("#")]
    toml_text = "\n".join(toml_lines)
    parsed = tomllib.loads(toml_text)
    assert CODEX_TOP_LEVEL_KEY in parsed
    server = parsed[CODEX_TOP_LEVEL_KEY][CODEX_SERVER_NAME]
    assert "command" in server
    assert "args" in server


def test_print_agent_config_codex_does_not_leak_secrets() -> None:
    """print_agent_config('codex') output must not contain secret keywords."""
    buf = io.StringIO()
    print_agent_config("codex", file=buf)
    output_lower = buf.getvalue().lower()
    _secret_patterns = ("password", "token", "api_key", "secret", "credential")
    for pattern in _secret_patterns:
        assert pattern not in output_lower, (
            f"Output must not contain secret-related keyword: {pattern!r}"
        )


def test_print_agent_config_codex_does_not_write_files(tmp_path: Path) -> None:
    """print_agent_config('codex') must not create or modify any files."""
    # Record the state of a temp directory before the call
    before = set(tmp_path.iterdir())
    buf = io.StringIO()
    print_agent_config("codex", file=buf)
    after = set(tmp_path.iterdir())
    assert before == after, (
        "print_agent_config must not write any files; "
        f"new files found: {after - before}"
    )


def test_print_agent_config_unknown_target_raises() -> None:
    """print_agent_config() must raise ValueError for unknown targets."""
    with pytest.raises(ValueError, match="Unknown agent target"):
        print_agent_config("cursor", file=io.StringIO())


def test_print_agent_config_codex_case_insensitive() -> None:
    """Target name matching must be case-insensitive."""
    buf1 = io.StringIO()
    buf2 = io.StringIO()
    print_agent_config("codex", file=buf1)
    print_agent_config("CODEX", file=buf2)
    assert buf1.getvalue() == buf2.getvalue()


def test_print_agent_config_codex_output_mentions_stdio_transport() -> None:
    """Output comment must state that Codex uses stdio (subprocess) transport."""
    buf = io.StringIO()
    print_agent_config("codex", file=buf)
    output_lower = buf.getvalue().lower()
    assert "stdio" in output_lower or "subprocess" in output_lower, (
        "Output must mention the stdio/subprocess transport so users understand "
        "that Codex launches modal-mcp directly"
    )


# ===========================================================================
# install_codex_config() — actual install mechanics
# ===========================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_codex_dir(tmp_path: Path) -> Path:
    """Create a temporary ~/.codex directory and return it."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    return codex_dir


def _codex_config(codex_dir: Path) -> Path:
    """Return the path to config.toml within a codex dir."""
    return codex_dir / "config.toml"


_ABS_ENV = "/home/user/project/.env"
_CUSTOM_CMD = "/opt/modal/bin/modal-mcp"


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_install_dry_run_returns_dry_run(tmp_path: Path) -> None:
    """install_codex_config(dry_run=True) returns 'dry_run'."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    buf = io.StringIO()
    result = install_codex_config(
        env_file=_ABS_ENV,
        dry_run=True,
        config_path=cfg,
        file=buf,
    )
    assert result == "dry_run"


def test_install_dry_run_prints_target_file(tmp_path: Path) -> None:
    """setup --install codex --dry-run: output contains the target file path."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    buf = io.StringIO()
    install_codex_config(
        env_file=_ABS_ENV,
        dry_run=True,
        config_path=cfg,
        file=buf,
    )
    output = buf.getvalue()
    assert str(cfg) in output, "Dry-run output must include the target config file path"


def test_install_dry_run_prints_exact_change(tmp_path: Path) -> None:
    """setup --install codex --dry-run: output describes the exact change."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    buf = io.StringIO()
    install_codex_config(
        env_file=_ABS_ENV,
        dry_run=True,
        config_path=cfg,
        file=buf,
    )
    output = buf.getvalue()
    # Must mention the mcp_servers entry and command
    assert "mcp_servers" in output or "modal-mcp" in output
    # Must contain the env-file path
    assert _ABS_ENV in output


def test_install_dry_run_does_not_write_files(tmp_path: Path) -> None:
    """Dry-run must not create or modify any files."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    before = set(codex_dir.iterdir())
    install_codex_config(
        env_file=_ABS_ENV,
        dry_run=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    after = set(codex_dir.iterdir())
    assert before == after, f"Dry-run must not write files; new: {after - before}"


def test_install_dry_run_is_valid_toml_block(tmp_path: Path) -> None:
    """Dry-run output contains a parseable [mcp_servers.modal-mcp] block."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    buf = io.StringIO()
    install_codex_config(
        env_file=_ABS_ENV,
        dry_run=True,
        config_path=cfg,
        file=buf,
    )
    output = buf.getvalue()
    # Extract TOML lines (skip non-TOML header lines that start with known prefixes)
    toml_lines = [
        ln
        for ln in output.splitlines()
        if not ln.startswith("Target:")
        and not ln.startswith("Change:")
        and not ln.startswith("Would add")
    ]
    toml_text = "\n".join(toml_lines)
    if toml_text.strip():
        tomllib.loads(toml_text)


# ---------------------------------------------------------------------------
# Happy path — fresh install
# ---------------------------------------------------------------------------


def test_install_returns_installed_on_success(tmp_path: Path) -> None:
    """install_codex_config returns 'installed' when config is written."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    result = install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    assert result == "installed"


def test_install_creates_config_when_absent(tmp_path: Path) -> None:
    """install_codex_config creates config.toml when the file does not exist."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    assert not cfg.exists()
    install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    assert cfg.is_file()


def test_install_written_config_is_valid_toml(tmp_path: Path) -> None:
    """Written config.toml must be parseable by tomllib."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    parsed = tomllib.loads(cfg.read_text())
    assert CODEX_TOP_LEVEL_KEY in parsed
    server = parsed[CODEX_TOP_LEVEL_KEY][CODEX_SERVER_NAME]
    assert server["command"] == CODEX_SERVER_COMMAND
    assert "--env-file" in server["args"]
    assert _ABS_ENV in server["args"]


def test_install_written_command_is_absolute_env_file(tmp_path: Path) -> None:
    """Installed config args must contain an absolute --env-file path."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    parsed = tomllib.loads(cfg.read_text())
    args = parsed[CODEX_TOP_LEVEL_KEY][CODEX_SERVER_NAME]["args"]
    idx = args.index("--env-file")
    env_path = args[idx + 1]
    assert Path(env_path).is_absolute(), (
        f"--env-file path in installed config must be absolute; got: {env_path!r}"
    )


def test_install_preserves_unrelated_config(tmp_path: Path) -> None:
    """install_codex_config preserves unrelated TOML keys in existing config."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    # Write existing config with unrelated keys
    existing = (
        '[other_section]\nsome_key = "some_value"\n\n[another_thing]\nflag = true\n'
    )
    cfg.write_text(existing)
    install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    parsed = tomllib.loads(cfg.read_text())
    # Unrelated keys must survive
    assert parsed["other_section"]["some_key"] == "some_value"
    assert parsed["another_thing"]["flag"] is True
    # New entry must be present
    assert CODEX_TOP_LEVEL_KEY in parsed
    assert CODEX_SERVER_NAME in parsed[CODEX_TOP_LEVEL_KEY]


def test_install_preserves_other_mcp_servers(tmp_path: Path) -> None:
    """install_codex_config does not overwrite other mcp_servers entries."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    # Write existing config with another MCP server
    existing = '[mcp_servers.other-server]\ncommand = "other-mcp"\nargs = []\n'
    cfg.write_text(existing)
    install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    parsed = tomllib.loads(cfg.read_text())
    # Other server must still be present
    assert "other-server" in parsed[CODEX_TOP_LEVEL_KEY]
    # New server must also be present
    assert CODEX_SERVER_NAME in parsed[CODEX_TOP_LEVEL_KEY]


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def test_install_creates_backup_when_file_exists(tmp_path: Path) -> None:
    """install_codex_config creates a timestamped backup of an existing config."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    original_content = '[other]\nkey = "value"\n'
    cfg.write_text(original_content)

    install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
        _timestamp="20260419T103000",
    )

    backup = codex_dir / "config.toml.bak.20260419T103000"
    assert backup.is_file(), (
        "Backup file must be created when existing config is present"
    )
    assert backup.read_text() == original_content, (
        "Backup must contain the original content"
    )


def test_install_backup_filename_uses_timestamp_from_constant(tmp_path: Path) -> None:
    """Backup filename matches CODEX_BACKUP_SUFFIX_TEMPLATE pattern."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    cfg.write_text("[x]\ny = 1\n")
    install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
        _timestamp="20260419T120000",
    )
    suffix = CODEX_BACKUP_SUFFIX_TEMPLATE.format(timestamp="20260419T120000")
    expected_backup = codex_dir / (CODEX_CONFIG_FILENAME + suffix)
    assert expected_backup.is_file()


def test_install_no_backup_when_config_absent(tmp_path: Path) -> None:
    """No backup file is created when the config does not yet exist."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
        _timestamp="20260419T103000",
    )
    # Only config.toml should exist, no backup
    files = list(codex_dir.iterdir())
    assert len(files) == 1
    assert files[0].name == CODEX_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_install_is_idempotent(tmp_path: Path) -> None:
    """Running install twice returns 'already_installed' on the second run."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    # First install
    r1 = install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    assert r1 == "installed"
    # Second install (same args)
    r2 = install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    assert r2 == "already_installed"


def test_install_idempotent_does_not_create_backup_second_run(tmp_path: Path) -> None:
    """Idempotent re-run must not create an additional backup."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
        _timestamp="20260419T103000",
    )
    files_after_first = set(f.name for f in codex_dir.iterdir())
    install_codex_config(
        env_file=_ABS_ENV,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
        _timestamp="20260419T110000",
    )
    files_after_second = set(f.name for f in codex_dir.iterdir())
    assert files_after_second == files_after_first, (
        "Idempotent re-run must not add any new files"
    )


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------


def test_install_yes_flag_skips_prompt(tmp_path: Path) -> None:
    """yes=True must install without calling input()."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    with patch("builtins.input") as mock_input:
        install_codex_config(
            env_file=_ABS_ENV,
            yes=True,
            config_path=cfg,
            file=io.StringIO(),
        )
    mock_input.assert_not_called()


def test_install_confirmation_decline_returns_declined(tmp_path: Path) -> None:
    """Responding 'n' to the confirmation prompt returns 'declined'."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    with patch("builtins.input", return_value="n"):
        result = install_codex_config(
            env_file=_ABS_ENV,
            config_path=cfg,
            file=io.StringIO(),
        )
    assert result == "declined"
    assert not cfg.exists(), "Declined install must not write any files"


def test_install_confirmation_accept_returns_installed(tmp_path: Path) -> None:
    """Responding 'y' to the confirmation prompt returns 'installed'."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    with patch("builtins.input", return_value="y"):
        result = install_codex_config(
            env_file=_ABS_ENV,
            config_path=cfg,
            file=io.StringIO(),
        )
    assert result == "installed"
    assert cfg.is_file()


def test_install_confirmation_eof_returns_declined(tmp_path: Path) -> None:
    """EOFError on input (non-interactive) treats as 'n' and returns 'declined'."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    with patch("builtins.input", side_effect=EOFError):
        result = install_codex_config(
            env_file=_ABS_ENV,
            config_path=cfg,
            file=io.StringIO(),
        )
    assert result == "declined"


# ---------------------------------------------------------------------------
# Refusal conditions
# ---------------------------------------------------------------------------


def test_install_refuses_when_config_dir_missing(tmp_path: Path) -> None:
    """install_codex_config raises CodexInstallError if config dir doesn't exist."""
    cfg = tmp_path / "nonexistent" / "config.toml"
    with pytest.raises(CodexInstallError, match="does not exist"):
        install_codex_config(
            env_file=_ABS_ENV,
            yes=True,
            config_path=cfg,
            file=io.StringIO(),
        )


def test_install_refuses_symlink_config(tmp_path: Path) -> None:
    """install_codex_config raises CodexInstallError if config file is a symlink."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    # Create a symlink pointing to a temp target
    target = tmp_path / "real_config.toml"
    target.write_text("")
    cfg.symlink_to(target)
    with pytest.raises(CodexInstallError, match="symlink"):
        install_codex_config(
            env_file=_ABS_ENV,
            yes=True,
            config_path=cfg,
            file=io.StringIO(),
        )


def test_install_refuses_non_regular_file(tmp_path: Path) -> None:
    """install_codex_config raises CodexInstallError if config path is a directory."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    # Make the config path a directory (not a regular file)
    cfg.mkdir()
    with pytest.raises(CodexInstallError, match="regular file"):
        install_codex_config(
            env_file=_ABS_ENV,
            yes=True,
            config_path=cfg,
            file=io.StringIO(),
        )


def test_install_refuses_unparseable_toml(tmp_path: Path) -> None:
    """install_codex_config raises CodexInstallError if existing TOML is malformed."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    cfg.write_text("this is [not valid toml {\n")
    with pytest.raises(CodexInstallError, match=r"[Pp]ars"):
        install_codex_config(
            env_file=_ABS_ENV,
            yes=True,
            config_path=cfg,
            file=io.StringIO(),
        )


def test_install_refuses_conflicting_entry(tmp_path: Path) -> None:
    """install_codex_config raises CodexInstallError for incompatible existing entry."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    # Write a mcp_servers.modal-mcp with a different command
    cfg.write_text(
        '[mcp_servers.modal-mcp]\ncommand = "some-other-command"\nargs = []\n'
    )
    with pytest.raises(CodexInstallError, match="incompatible"):
        install_codex_config(
            env_file=_ABS_ENV,
            yes=True,
            config_path=cfg,
            file=io.StringIO(),
        )


def test_install_refuses_mcp_servers_non_table(tmp_path: Path) -> None:
    """install_codex_config raises CodexInstallError if mcp_servers is not a table."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    # mcp_servers as a plain string (invalid structure)
    cfg.write_text('mcp_servers = "not a table"\n')
    with pytest.raises(CodexInstallError, match=r"[Tt]able|table"):
        install_codex_config(
            env_file=_ABS_ENV,
            yes=True,
            config_path=cfg,
            file=io.StringIO(),
        )


def test_install_requires_absolute_env_file(tmp_path: Path) -> None:
    """install_codex_config raises ValueError if env_file is relative."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    with pytest.raises(ValueError, match="absolute path"):
        install_codex_config(
            env_file="relative/.env",
            yes=True,
            config_path=cfg,
            file=io.StringIO(),
        )


# ---------------------------------------------------------------------------
# Validation failure — filesystem recovery
# ---------------------------------------------------------------------------


def test_install_validation_failure_removes_file_when_no_backup(tmp_path: Path) -> None:
    """Validation failure on fresh install removes the written file.

    When no prior config existed (no backup was created), a post-write
    validation failure must unlink the freshly-written file so that the
    filesystem is returned to its pre-install state.  This is a regression
    test for the case where ``backup_path is None`` after a validation error
    (finding-2 in repair cycle mm-ahc9).
    """
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    assert not cfg.exists(), "Pre-condition: config must not exist before install"

    # Inject a validation error by making tomllib.loads raise on every call.
    # For a fresh install (no existing config) tomllib.loads is only reached in
    # the post-write validation step; there is no prior-content TOML parse.
    with (
        patch(
            "modal_mcp.agent_targets.codex.tomllib.loads",
            side_effect=tomllib.TOMLDecodeError("injected"),
        ),
        pytest.raises(CodexInstallError, match=r"[Vv]alidation"),
    ):
        install_codex_config(
            env_file=_ABS_ENV,
            yes=True,
            config_path=cfg,
            file=io.StringIO(),
        )

    # The freshly-written (now invalid) file must have been removed so the
    # filesystem is back to its pre-install state.
    assert not cfg.exists(), (
        "Validation failure with no prior config must remove the written file; "
        f"{cfg} still exists on disk"
    )


def test_install_validation_failure_with_backup_restores_original(
    tmp_path: Path,
) -> None:
    """Validation failure with an existing config restores the backup.

    When a prior config existed (backup was created), a post-write validation
    failure must restore the original content from the backup rather than
    removing the file.
    """
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    original_content = '[other]\nkey = "value"\n'
    cfg.write_text(original_content)

    # Inject a validation error in the post-write step.
    with (
        patch(
            "modal_mcp.agent_targets.codex.tomllib.loads",
            side_effect=tomllib.TOMLDecodeError("injected"),
        ),
        pytest.raises(CodexInstallError, match=r"[Vv]alidation"),
    ):
        install_codex_config(
            env_file=_ABS_ENV,
            yes=True,
            config_path=cfg,
            file=io.StringIO(),
            _timestamp="20260420T000000",
        )

    # Config must still exist and contain the original content (restored from backup).
    assert cfg.exists(), "Config must still exist after backup restore"
    assert cfg.read_text() == original_content, (
        "Config must contain the original content after backup restore"
    )


# ---------------------------------------------------------------------------
# Custom command
# ---------------------------------------------------------------------------


def test_install_custom_command_is_registered(tmp_path: Path) -> None:
    """install_codex_config embeds a custom command in the written config."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    install_codex_config(
        env_file=_ABS_ENV,
        command=_CUSTOM_CMD,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    parsed = tomllib.loads(cfg.read_text())
    assert parsed[CODEX_TOP_LEVEL_KEY][CODEX_SERVER_NAME]["command"] == _CUSTOM_CMD


def test_install_custom_command_idempotent(tmp_path: Path) -> None:
    """Idempotency check uses the custom command for comparison."""
    codex_dir = _make_codex_dir(tmp_path)
    cfg = _codex_config(codex_dir)
    install_codex_config(
        env_file=_ABS_ENV,
        command=_CUSTOM_CMD,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    r2 = install_codex_config(
        env_file=_ABS_ENV,
        command=_CUSTOM_CMD,
        yes=True,
        config_path=cfg,
        file=io.StringIO(),
    )
    assert r2 == "already_installed"
