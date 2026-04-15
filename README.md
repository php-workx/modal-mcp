# Modal MCP

Self-hosted Model Context Protocol server for read-only Modal operations.

The v1 server exposes Modal discovery, app, deployment, log, container, volume,
and sandbox read APIs over FastMCP Streamable HTTP at `/mcp`. Mutating `change`
and `expert` toolsets are registered as disabled stubs, hidden from default
`tools/list`, and guarded by policy code for future releases.

## Quickstart

```bash
uv sync --extra dev
export MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765
export MODAL_MCP_SIGNING_KEYS=kid1:$(openssl rand -hex 32)
uv run modal-mcp
```

For production-like use, prefer file-backed secrets:

```bash
install -m 600 /dev/null .secrets/modal-token-id
install -m 600 /dev/null .secrets/modal-token-secret
install -m 600 /dev/null .secrets/modal-mcp-signing-key

export MODAL_TOKEN_ID_FILE=$PWD/.secrets/modal-token-id
export MODAL_TOKEN_SECRET_FILE=$PWD/.secrets/modal-token-secret
export MODAL_MCP_SIGNING_KEY_FILE=$PWD/.secrets/modal-mcp-signing-key
export MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765
uv run modal-mcp
```

Use a Modal service-user token with Viewer permissions in a restricted,
non-production Modal environment for the default read-only posture.

## Configuration

Required for self-hosted startup:

- `MODAL_MCP_ALLOWED_ORIGINS`: comma-separated browser/client origins allowed to
  call `/mcp`.
- `MODAL_MCP_SIGNING_KEYS` or `MODAL_MCP_SIGNING_KEY_FILE`: one or more
  `kid:hex` HMAC signing keys for refs, cursors, and approval tokens.
- Modal credentials from either `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`, their
  `_FILE` variants, or an existing `MODAL_CONFIG_PATH`.

Recommended defaults:

- `MODAL_MCP_READ_ONLY=true`
- `MODAL_MCP_ENABLED_TOOLSETS=discovery,apps,containers,logs,volumes,sandboxes`
- `MODAL_MCP_AUDIT_LOG=stdout`
- `MODAL_MCP_HTTP_BIND=127.0.0.1:8765`

Hosted OAuth delegation for Modal tokens is not part of v1. Cloudflare Workers
and other edge runtimes are unsupported targets because the server depends on
the Modal Python SDK, local process hardening, and FastMCP Streamable HTTP
lifespan behavior.

## Security Posture

The server enforces Origin and Host allowlists before MCP handling. Policy
middleware applies rate limits, toolset gates, read-only gates, approval gates
for future mutations, audit events, and output redaction. Known secrets are
redacted after exception formatting and before JSON rendering.

Approval tokens are HMAC signed, session-bound, single-use, and backed by an
approval ledger. In v1, change and expert tools remain disabled by default; the
approval path is present so future mutating tools can keep the same contract.

See [docs/self-hosting.md](docs/self-hosting.md) and
[docs/threat-model.md](docs/threat-model.md).

## Validation

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Live Modal tests are opt-in:

```bash
MODAL_MCP_LIVE=1 MODAL_ENVIRONMENT=dev uv run pytest tests/integration/live -q
```
