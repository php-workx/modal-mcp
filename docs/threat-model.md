# Modal MCP Threat Model

This document tracks the v1 priority threats from `docs/specs/modal-mcp_v2.md`
section 10.2 and the Python controls from section 10.3. It is an implementation
checklist: every mitigation names the ticket or test evidence that should keep
the control from drifting.

## Priority Threats

### Credential exfiltration

Threat: Modal tokens, hosted session tokens, signing keys, or derived secrets leak
through logs, exception formatting, audit JSONL, subprocess environments, or tool
output.

Mitigations:
- Known-secret and shape-based redaction runs after `format_exc_info` and before
  `JSONRenderer`, so traceback strings are scrubbed before they reach sinks.
- `Settings` stores Modal credentials and signing material as `SecretStr`.
- Startup scrubs `MODAL_TOKEN_*` and signing-key environment variables after the
  process has loaded them.
- Runtime hardening disables core dumps where the host supports
  `prctl(PR_SET_DUMPABLE, 0)`.
- The CLI fallback is dead code by default, rejects hosted modes, and uses a
  strict environment whitelist when enabled.
- Hosted persistence uses envelope encryption as specified in the v2 storage
  contract.

Evidence:
- Tickets: `mm-e30t`, `mm-5rm8`, `mm-tqrt`, `mm-bbfm`.
- Tests: `tests/unit/test_config.py`, `tests/integration/test_security.py`,
  `tests/unit/test_redaction.py`, `tests/integration/test_observability.py`,
  `tests/unit/test_cli_fallback.py`.

### Scope confusion

Threat: an operator or client reuses a reference against the wrong workspace,
environment, app, or target resource.

Mitigations:
- `Ref`, cursor, and approval payloads are HMAC signed over deterministic CBOR and
  include scope fields such as `kind`, `env`, `ws`, version, expiry, and
  resource-specific identifiers.
- Approval tokens bind `tool_name`, sorted target refs, actor, workspace,
  `mcp_session_id`, `auth_session_id`, nonce, expiry, not-before time, and remote
  mode.
- `MODAL_MCP_ALLOW_CROSS_ENV=false` is the default; cross-env mode must not
  disable signed ref environment equality.
- Audit events carry policy decisions and session context so scope decisions can
  be reviewed after the fact.

Evidence:
- Tickets: `mm-2uwn`, `mm-j03r`, `mm-b30b`, `mm-mmjd`, `mm-tqrt`.
- Tests: `tests/unit/test_refs.py`, `tests/unit/test_policy.py`,
  `tests/integration/test_observability.py`.

### Supply chain drift

Threat: a dependency or Modal release changes behavior, removes an internal API,
or introduces a vulnerability that silently invalidates adapter assumptions.

Mitigations:
- Modal and FastMCP versions are pinned by project dependency constraints and the
  lockfile.
- CI should run dependency review, CodeQL, and `pip-audit`.
- Internal Modal API inventory is captured as executable capability metadata and
  contract tests.
- Drift probes should run against both the pinned Modal version and the latest
  compatible Modal version before release.

Evidence:
- Tickets: `mm-58gz`, `mm-i8fy`, `mm-624h`, `mm-e30t`.
- Tests: `tests/contract/test_modal_symbols.py`.

### Upstream Modal format drift

Threat: internal gRPC stubs, `modal._logs`, or object shapes change between Modal
releases and cause parsing, pagination, or lifecycle assumptions to become wrong.

Mitigations:
- Adapter capability metadata records the exact Modal symbols and behavior used
  by v1.
- Fixture replay tests and drift probes validate expected response shapes.
- The CLI fallback stays unavailable by default but exists as a last-resort
  recovery path if a Modal release breaks an internal API.

Evidence:
- Tickets: `mm-58gz`, `mm-i8fy`, `mm-624h`, `mm-bbfm`.
- Tests: `tests/contract/test_modal_symbols.py`,
  `tests/unit/test_cli_fallback.py`.

### Irreversible operations

Threat: mutating operations such as app stop, rollback, container stop, sandbox
termination, or future expert operations cause production impact that cannot be
automatically undone.

Mitigations:
- Mutating tools are in a local allowlist and are never authorized from MCP tool
  annotations such as `destructiveHint`.
- Read-only mode blocks `change` and `expert` toolsets.
- Mutations require dry-run plans, explicit impact text, out-of-band human
  approval, not-before timing, single-use approval tokens, and per-actor mutation
  rate limits.
- The approval ledger uses an append-only fsynced file for self-hosted persistence
  and reserves Redis `SET NX PX` semantics for hosted or multi-worker mode.
- Audit events record approval issuance, approval, consumption, policy denials,
  and redacted results.

Evidence:
- Tickets: `mm-98r8`, `mm-b30b`, `mm-mmjd`, `mm-tqrt`.
- Tests: `tests/unit/test_policy.py`, `tests/integration/test_observability.py`.

### Tool-metadata poisoning

Threat: model-visible metadata, especially `modal_discovery_server_info`, carries
operator-supplied text that changes tool-selection reasoning or injects
instructions into the LLM context.

Mitigations:
- Discovery output is fixed-schema only: enum mode, boolean `read_only`,
  closed-set toolsets, version fields, and no operator-supplied free text.
- Policy enforcement never trusts model-visible tool annotations for
  authorization.
- If an operator banner is added later, it must live in a separate capped
  `server_info.banner` field with client guidance to display it to the human
  rather than inject it into the model context.

Evidence:
- Tickets: `mm-brby`, `mm-korx`, `mm-mmjd`.
- Tests: `tests/unit/test_policy.py`.

## Python Controls

### Subprocess use

The default Modal data path does not use `subprocess`. The fallback adapter is
dead code unless `MODAL_MCP_CLI_FALLBACK=true`; hosted modes refuse that flag.
When the fallback is enabled it must use `subprocess.run(args=[...])`, never a
shell string, with strict subcommand allowlists, timeouts, capped stdout, and an
explicit environment whitelist.

Evidence: `mm-bbfm`, `tests/unit/test_cli_fallback.py`.

### No eval, exec, or pickle on client input

No `eval`, `exec`, or `pickle` may process client input. Tool arguments and
policy-only fields are parsed through Pydantic models or `TypeAdapter` checks.
The future expert toolset uses a constrained plan DSL, not arbitrary Python
source.

Evidence: `mm-mmjd`, `tests/unit/test_policy.py`.

### Token MACs

Refs, cursors, and approval tokens use HMAC-SHA256 over RFC 8949 Core
Deterministic CBOR. The MAC input is:

```text
HMAC-SHA256(
    K,
    "modal-mcp/v1" || 0x00 ||
    type_tag || 0x00 ||
    keyid || 0x00 ||
    canonical_cbor_deterministic(payload)
)
```

Verification rejects non-canonical encodings and uses `hmac.compare_digest`.
`MODAL_MCP_SIGNING_KEYS` is a comma-separated `kid:hex` list; the first key
signs and all listed keys verify.

Evidence: `mm-2uwn`, `mm-j03r`, `tests/unit/test_refs.py`.

### Credential process controls

Credentials are read into `SecretStr`, secret environment variables are scrubbed
after startup, core dumps are suppressed where supported, and fallback subprocess
paths must pass a whitelist `env` rather than inheriting `os.environ`.

Evidence: `mm-5rm8`, `mm-bbfm`, `tests/unit/test_config.py`,
`tests/integration/test_security.py`, `tests/unit/test_cli_fallback.py`.

### asyncio task supervision

Long-running work such as log tailing and sandbox stdio streaming must be named,
cancelled cleanly, and tied to server shutdown. No orphan background tasks are
allowed.

Evidence: planned streaming/tool implementation tickets and future tests for log
tail and sandbox stdio lifecycle.
