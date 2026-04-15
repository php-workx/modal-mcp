# Self-Hosting Modal MCP

This guide describes the supported v1 deployment path: a self-hosted,
read-only Modal MCP server running FastMCP Streamable HTTP at `/mcp`.

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
- OAuth delegation for Modal tokens.
- Hosted multi-tenant credential storage.
- Enabling mutating operations by default.

## 15-Minute Setup

1. Create a Modal service-user token with Viewer permissions scoped to a
   restricted, non-production environment.
2. Install dependencies:

   ```bash
   uv sync --extra dev
   ```

3. Create file-backed secrets:

   ```bash
   mkdir -p .secrets
   printf '%s' '<modal-token-id>' > .secrets/modal-token-id
   printf '%s' '<modal-token-secret>' > .secrets/modal-token-secret
   printf 'kid1:%s' "$(openssl rand -hex 32)" > .secrets/modal-mcp-signing-key
   chmod 600 .secrets/*
   ```

4. Export runtime configuration:

   ```bash
   export MODAL_TOKEN_ID_FILE=$PWD/.secrets/modal-token-id
   export MODAL_TOKEN_SECRET_FILE=$PWD/.secrets/modal-token-secret
   export MODAL_MCP_SIGNING_KEY_FILE=$PWD/.secrets/modal-mcp-signing-key
   export MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765
   export MODAL_MCP_ALLOWED_HOSTS=127.0.0.1,localhost
   export MODAL_ENVIRONMENT=dev
   ```

5. Start the server:

   ```bash
   uv run modal-mcp
   ```

6. Configure the MCP client to use Streamable HTTP at:

   ```text
   http://127.0.0.1:8765/mcp
   ```

## Required Environment Variables

- `MODAL_MCP_ALLOWED_ORIGINS`: required. The server rejects missing, `null`,
  cross-site, and unlisted origins before MCP handling.
- `MODAL_MCP_SIGNING_KEYS` or `MODAL_MCP_SIGNING_KEY_FILE`: required. Values use
  `kid:hex` format; the first key signs and all keys verify.
- Modal credentials from `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`, the `_FILE`
  variants, or `MODAL_CONFIG_PATH`.

Recommended:

- `MODAL_MCP_AUDIT_LOG=stdout` or a JSONL file path.
- `MODAL_MCP_READ_ONLY=true`.
- `MODAL_MCP_ENABLED_TOOLSETS=discovery,apps,containers,logs,volumes,sandboxes`.
- `MODAL_MCP_RATE_LIMIT_RPS=5`.
- `MODAL_MCP_MUTATION_RATE_LIMIT_SECONDS=30`.

## Auth And Policy

Self-hosted use can run with Modal credentials only, or with a static MCP bearer
token by setting `MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE`.

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

Live Modal smoke tests are disabled unless explicitly requested:

```bash
MODAL_MCP_LIVE=1 \
MODAL_ENVIRONMENT=dev \
MODAL_TOKEN_ID=... \
MODAL_TOKEN_SECRET=... \
MODAL_MCP_SIGNING_KEYS=kid1:... \
uv run pytest tests/integration/live/test_modal_live.py -q
```

The live environment must be non-production and safe for read-only inventory
calls.
