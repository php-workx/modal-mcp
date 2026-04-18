# Self-Hosting Modal MCP

This guide describes the supported v1 deployment path: a self-hosted,
read-only Modal MCP server running FastMCP Streamable HTTP at `/mcp` via
Docker Compose only.

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

## 15-Minute Setup

There are two credential paths. Choose the one that matches your setup.

### Path A — existing `~/.modal.toml`

If your machine already has `~/.modal.toml` (created by `modal token set` or
`modal token new`), the server uses it automatically. No token files are
needed.

1. Install dependencies:

   ```bash
   uv sync --extra dev
   ```

2. Generate a signing key and start the server:

   ```bash
   export MODAL_MCP_SIGNING_KEYS=kid1:$(openssl rand -hex 32)
   export MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765
   export MODAL_MCP_ALLOWED_HOSTS=127.0.0.1,localhost
   export MODAL_ENVIRONMENT=dev
   uv run modal-mcp
   ```

### Path B — file-backed service-user token

For a clean machine or a dedicated Modal service-user token with Viewer
permissions scoped to a restricted, non-production environment:

1. Install dependencies:

   ```bash
   uv sync --extra dev
   ```

2. Create file-backed secrets with non-empty token values:

   ```bash
   mkdir -p .secrets
   printf '%s' '<modal-token-id>'     > .secrets/modal-token-id
   printf '%s' '<modal-token-secret>' > .secrets/modal-token-secret
   printf 'kid1:%s' "$(openssl rand -hex 32)" > .secrets/modal-mcp-signing-key
   chmod 600 .secrets/*
   ```

3. Export runtime configuration:

   ```bash
   export MODAL_TOKEN_ID_FILE=$PWD/.secrets/modal-token-id
   export MODAL_TOKEN_SECRET_FILE=$PWD/.secrets/modal-token-secret
   export MODAL_MCP_SIGNING_KEY_FILE=$PWD/.secrets/modal-mcp-signing-key
   export MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765
   export MODAL_MCP_ALLOWED_HOSTS=127.0.0.1,localhost
   export MODAL_ENVIRONMENT=dev
   ```

4. Start the server:

   ```bash
   uv run modal-mcp
   ```

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
