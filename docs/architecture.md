# Architecture

Modal MCP v1 is a local read-only bridge between an MCP client and the Modal
Python SDK.

```text
MCP client
  |
  | stdio, Streamable HTTP, or SSE
  v
modal-mcp local server
  |
  | policy, auth, rate limits, audit, redaction
  v
Modal SDK adapter
  |
  | Modal credentials from ~/.modal.toml or file-backed token env vars
  v
Modal API
```

## Process Model

There are two supported local connection styles:

- Stdio: the MCP client launches `modal-mcp run --env-file <path>` as a
  subprocess. Codex uses this path.
- HTTP/SSE: the user starts `uv run modal-mcp run --env-file <path>` from a
  source checkout, or `modal-mcp run --env-file <path>` when installed. The MCP
  client connects to `http://127.0.0.1:8765/mcp` or `/mcp/sse`. Claude Desktop
  uses the SSE path.

The server is not a hosted relay. Modal credentials stay on the user's machine.

## Configuration Loading

The server reads `.env` by default and `modal-mcp run --env-file <path>` can
load an explicit file before startup. Real environment variables override
values loaded from `.env`.

`modal-mcp setup --yes` writes only non-credential server settings and a
signing-key path. It does not write Modal credentials or pin
`MODAL_ENVIRONMENT`.

## Request Path

For HTTP/SSE clients, requests pass through:

1. Host and Origin guards.
2. Optional static bearer-token auth for non-localhost exposure.
3. FastMCP tool routing.
4. Policy middleware with rate limiting, toolset gates, read-only gates, and
   approval gates for disabled future mutations.
5. Audit logging and redaction before output leaves the server.

For stdio clients, the MCP server runs inside the launched subprocess and tool
calls still pass through toolset, read-only, audit, and redaction policy.

## Data Boundaries

Modal MCP does not persist Modal API data. The durable local files are setup and
operator-controlled state:

- `.env` for server configuration.
- `.secrets/signing-key.txt` for internal signing.
- Optional file-backed Modal token files if the user creates them.
- Optional audit JSONL file when `MODAL_MCP_AUDIT_LOG` points to a path.
- Optional approval ledger for future disabled-by-default mutation flows.

Logs, volume text, and sandbox stdio can contain application-sensitive data.
They are read-only but should still be treated as sensitive output.

## Non-Goals In V1

- Hosted OAuth delegation.
- Multi-tenant credential storage.
- Enabling mutating Modal operations.
- Cloudflare Workers or other edge runtime deployment.
- Helm or Kubernetes packaging.
