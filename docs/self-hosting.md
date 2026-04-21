# Self-Hosting Modal MCP

This guide describes the supported v1 deployment path: a self-hosted,
read-only Modal MCP server running FastMCP Streamable HTTP at `/mcp` on a
local machine or a self-managed host.

## Scope

v1 is intentionally read-only. The enabled toolsets are:

- `discovery`
- `apps`
- `containers`
- `logs`
- `volumes`
- `sandboxes`

The `change` and `expert` toolsets are registered only as disabled stubs. They
are hidden from default `tools/list`, blocked by read-only policy, and return
explicit disabled-capability errors if invoked internally.

Unsupported v1 targets and non-goals:

- Cloudflare Workers or edge runtimes.
- hosted OAuth delegation for Modal tokens.
- `/session/create` and hosted credential storage (multi-tenant flows).
- Enabling mutating operations by default.
- Kubernetes/Helm packaging (deferred to v2/v3 decision gate).

## CLI Setup

`modal-mcp setup` is the recommended starting point. It generates the
non-credential server config and a signing key without touching Modal
credentials.

The examples use `uv run modal-mcp` so they work from a source checkout without
activating `.venv`. If you have installed the package into your active shell,
`modal-mcp` can be used directly.

### Step 1 — Generate local config

```bash
uv sync --extra dev
uv run modal-mcp setup --yes
```

`setup --yes` creates two files (idempotent — existing files are preserved):

| File                       | Mode   | Content                                                    |
|----------------------------|--------|------------------------------------------------------------|
| `.env`                     | `0600` | `MODAL_MCP_SIGNING_KEY_FILE` + `MODAL_MCP_ALLOWED_ORIGINS` |
| `.secrets/signing-key.txt` | `0600` | `kid:hex` HMAC signing key                                 |

The generated `.env` intentionally omits all Modal credential variables
(`MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `MODAL_ENVIRONMENT`).  Credentials
must be supplied separately (see credential paths below).

### Step 2 — Verify the installation

```bash
uv run modal-mcp doctor --env-file .env
```

`doctor` runs without loading the full server settings and reports on:

1. Package imports (`modal_mcp`, `modal`, `fastmcp`, `uvicorn`)
2. `.env` file presence
3. Signing key (inline or file-backed)
4. `MODAL_MCP_ALLOWED_ORIGINS` value
5. Read-only readiness (`MODAL_MCP_READ_ONLY`)
6. Enabled toolsets (`MODAL_MCP_ENABLED_TOOLSETS`)
7. Modal credential source (`~/.modal.toml`, env vars, or file-backed paths)
8. Modal SDK auth health (when credentials are present)
9. Modal CLI availability

Exit code is `0` when all checks pass; `3` when warnings exist but no check
fails (partial-ready); `1` when at least one check fails.

### Step 3 — Supply Modal credentials

Choose the credential path that matches your setup.  Either path can be used
alongside the `.env` generated in Step 1.

#### Path A — existing `~/.modal.toml`

If your machine already has `~/.modal.toml` (created by `modal token set` or
`modal token new`), the server uses it automatically. No token files are
needed.

> **Warning:** credentials in `~/.modal.toml` are typically personal or admin
> tokens with broad permissions.  `modal-mcp doctor` will warn you when this
> file is present.  For a stricter posture use Path B instead.

Export the environment config and start the server:

```bash
export MODAL_MCP_ALLOWED_HOSTS=127.0.0.1,localhost
export MODAL_ENVIRONMENT=dev
uv run modal-mcp run --env-file .env
```

#### Path B — file-backed service-user token (recommended)

Create a dedicated Modal service-user with Viewer permissions scoped to a
non-production workspace.  `modal-mcp setup` does **not** automatically create
or configure a scoped service-user token; you must create the service-user and
token yourself (Modal dashboard → Settings → Service users, or `modal token new`).

Store the token in file-backed secrets:

Replace the placeholder values with a real non-production service-user token:

```bash
mkdir -p .secrets
printf '%s' '<modal-token-id>'     > .secrets/modal-token-id
printf '%s' '<modal-token-secret>' > .secrets/modal-token-secret
chmod 600 .secrets/*
```

Export the file paths and start the server:

```bash
export MODAL_TOKEN_ID_FILE=$PWD/.secrets/modal-token-id
export MODAL_TOKEN_SECRET_FILE=$PWD/.secrets/modal-token-secret
export MODAL_MCP_ALLOWED_HOSTS=127.0.0.1,localhost
export MODAL_ENVIRONMENT=dev
uv run modal-mcp run --env-file .env
```

Never put `MODAL_TOKEN_ID` or `MODAL_TOKEN_SECRET` values directly in `.env`.
The setup-generated `.env` enforces this by design.

### MCP endpoint

Configure the MCP client to use Streamable HTTP at:

```text
http://127.0.0.1:8765/mcp
```

### Smoke checks

After the server starts, verify the connection with these read-only tool calls:

- `modal_discovery_server_info` — server metadata and enabled toolsets
- `modal_whoami` — authenticated Modal user identity
- `modal_list_environments` — Modal environments visible to the token
- `modal_list_apps` — apps in the configured environment

## Agent Integration

### Printing the config snippet

Print the config block for your agent client before writing any files:

```bash
# Codex CLI — stdio transport (Codex launches modal-mcp as a subprocess)
uv run modal-mcp print-agent-config --target codex --env-file "$PWD/.env"

# Claude Desktop — SSE transport (server must already be running)
uv run modal-mcp print-agent-config --target claude --env-file "$PWD/.env"
```

### Installing into Codex CLI

```bash
# Preview the change without writing anything
uv run modal-mcp setup --install codex --env-file "$PWD/.env" --dry-run

# Write the [mcp_servers.modal-mcp] entry to ~/.codex/config.toml
uv run modal-mcp setup --install codex --env-file "$PWD/.env" --yes
```

The install command:

- Requires `--env-file` to be an absolute path (so Codex can locate the
  settings regardless of its working directory at launch).
- Backs up the existing `~/.codex/config.toml` with a timestamped suffix
  before writing.
- Writes atomically and validates the round-trip before reporting success.
- Is idempotent: a no-op when the entry already exists with the correct
  command and args.
- Refuses if the config file is a symlink, is unparseable TOML, or already
  contains a conflicting `mcp_servers.modal-mcp` entry.

### Installing into Claude Desktop

Claude Desktop uses SSE transport.  The server must be started separately
before Claude connects.  Use `--install claude` to write the SSE URL entry
automatically:

```bash
# Preview the change without writing anything
uv run modal-mcp setup --install claude --dry-run

# Write the mcpServers.modal-mcp entry to claude_desktop_config.json
uv run modal-mcp setup --install claude --yes
```

The install command:

- Does **not** require `--env-file` (Claude Desktop connects over SSE; the
  env file is loaded by the separately-started server, not embedded in the
  config entry).
- Backs up the existing config with a timestamped suffix before writing.
- Writes atomically and validates the round-trip before reporting success.
- Is idempotent: a no-op when the entry already exists with the correct
  type and URL.
- Refuses if the config file is a symlink, is unparseable JSON, or already
  contains a conflicting `mcpServers.modal-mcp` entry.

macOS path: `~/Library/Application Support/Claude/claude_desktop_config.json`

### Legacy manual setup

The steps below work without the `modal-mcp setup` command if you prefer
to manage config by hand.

**Manual signing key (Path A variant):**

```bash
export MODAL_MCP_SIGNING_KEYS=kid1:$(openssl rand -hex 32)
export MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765
export MODAL_MCP_ALLOWED_HOSTS=127.0.0.1,localhost
export MODAL_ENVIRONMENT=dev
uv run modal-mcp run
```

**Manual file-backed secrets (Path B variant):**

Replace the placeholder values with a real non-production service-user token:

```bash
mkdir -p .secrets
printf '%s' '<modal-token-id>'     > .secrets/modal-token-id
printf '%s' '<modal-token-secret>' > .secrets/modal-token-secret
printf 'kid1:%s' "$(openssl rand -hex 32)" > .secrets/modal-mcp-signing-key
chmod 600 .secrets/*

export MODAL_TOKEN_ID_FILE=$PWD/.secrets/modal-token-id
export MODAL_TOKEN_SECRET_FILE=$PWD/.secrets/modal-token-secret
export MODAL_MCP_SIGNING_KEY_FILE=$PWD/.secrets/modal-mcp-signing-key
export MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765
export MODAL_MCP_ALLOWED_HOSTS=127.0.0.1,localhost
export MODAL_ENVIRONMENT=dev
uv run modal-mcp run
```

## Required Environment Variables

- `MODAL_MCP_ALLOWED_ORIGINS`: required. The server rejects missing, `null`,
  cross-site, and unlisted origins before MCP handling.
- `MODAL_MCP_SIGNING_KEYS` or `MODAL_MCP_SIGNING_KEY_FILE`: required. Values use
  `kid:hex` format; the first key signs and all keys verify.
- Modal credentials from `~/.modal.toml` (via `MODAL_CONFIG_PATH` or the
  default location), `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`, or the
  `MODAL_TOKEN_ID_FILE` / `MODAL_TOKEN_SECRET_FILE` variants pointing to
  non-empty files.

Recommended:

- `MODAL_MCP_AUDIT_LOG=stdout` or a JSONL file path.
- `MODAL_MCP_APPROVAL_LEDGER=/path/to/approvals.jsonl` when experimenting with
  disabled-by-default mutation flows; the ledger persists token digests and
  state only, never raw approval tokens or Modal credentials.
- `MODAL_MCP_READ_ONLY=true`.
- `MODAL_MCP_ENABLED_TOOLSETS=discovery,apps,containers,logs,volumes,sandboxes`.
- `MODAL_MCP_RATE_LIMIT_RPS=5`.
- `MODAL_MCP_MUTATION_RATE_LIMIT_SECONDS=30`.

## Auth And Policy

Self-hosted use can run with Modal credentials only. For non-localhost binds,
reverse-proxy exposure, or shared-machine scenarios, add a static MCP bearer
token by setting `MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE` to a path containing
a non-empty token value. This is optional for pure localhost use where network
access is already restricted.

Policy middleware runs on every tool call:

1. Rate limit.
2. Toolset gate.
3. Read-only gate.
4. Approval gate for future mutations.
5. Local Pydantic validation for policy-only fields.
6. Redaction before output leaves middleware.

Approval posture: v1 does not expose enabled mutations, but approval tokens and
the approval ledger are implemented for forward compatibility. Approval tokens
are HMAC signed, bound to actor/session/workspace/target refs, have not-before
and expiry checks, require out-of-band confirmation, and are single-use.
The approval route writes a non-usable `pending` ledger record before audit,
commits `approved` only after the approval audit event succeeds, and the policy
middleware consumes only `approved`. `pending`, `audit_failed`, and `consumed`
states are non-usable, including after process restart when
`MODAL_MCP_APPROVAL_LEDGER` is configured.

## Audit Log Format

Set `MODAL_MCP_AUDIT_LOG` to `stdout` or a file path. Events are JSONL and are
redacted before write. Typical fields include:

```json
{
  "ts": "2026-04-15T10:12:33+00:00",
  "type": "policy_decision",
  "tool": "modal_list_apps",
  "toolset": "apps",
  "decision": {
    "allowed": true,
    "code": "ALLOWED",
    "policy_version": "v1"
  },
  "mcp_session_id": "mcp-session-id"
}
```

## Validation Commands

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Live Modal smoke tests are disabled unless explicitly requested.

### Opt-In Live Read-Only Verification

`tests/integration/live/test_modal_live.py` is skipped by default. It runs only
when `MODAL_MCP_LIVE=1` is set. The test performs inventory-style read-only calls
only — listing environments and apps — and makes no content reads, no log reads,
no volume file reads, no sandbox stdio reads, and no mutations.

**Do not run it with production credentials.** Use a dedicated, non-production
Modal workspace with a Viewer-scoped service-user token. The account must have
at least one environment visible to the token; an empty environment list will
fail the assertion. An empty app list is accepted.

```bash
MODAL_MCP_LIVE=1 \
MODAL_ENVIRONMENT=dev \
MODAL_TOKEN_ID=<non-production-token-id> \
MODAL_TOKEN_SECRET=<non-production-token-secret> \
MODAL_MCP_SIGNING_KEYS=kid1:<hex-32-bytes> \
uv run pytest tests/integration/live/test_modal_live.py -q
```

What the test verifies:

- Modal credentials are accepted (auth validated).
- `list_environments()` returns at least one environment (the token must have
  visibility into at least one environment; an empty result fails the test).
- `list_apps(environment)` completes without error (inventory-style read-only;
  an empty result is accepted).

What the test explicitly avoids:

- Production credentials or production-dependent assertions.
- Content reads (log lines, volume file contents, sandbox stdio).
- Any mutating calls.
