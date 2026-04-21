# MCP Client Setup

Modal MCP can be connected to any MCP client that can launch a stdio server or
connect to a local HTTP/SSE endpoint. The built-in installer currently supports
Codex CLI and Claude Desktop.

Run setup first:

```bash
uv sync --extra dev
uv run modal-mcp setup --yes
uv run modal-mcp doctor --env-file .env
```

If you have installed the package into your active shell, `modal-mcp` can be
used directly. The examples use `uv run modal-mcp` so they work from a source
checkout without activating `.venv`.

## Codex CLI

Codex launches `modal-mcp` as a stdio subprocess.

Preview the config entry:

```bash
uv run modal-mcp setup --install codex --env-file "$PWD/.env" --dry-run
```

Install it:

```bash
uv run modal-mcp setup --install codex --env-file "$PWD/.env" --yes
```

The installer writes a `[mcp_servers.modal-mcp]` entry to
`~/.codex/config.toml`, backs up the previous config, writes atomically, and
validates the result before reporting success.

Manual equivalent:

```toml
[mcp_servers.modal-mcp]
command = "modal-mcp"
args = ["run", "--env-file", "/absolute/path/to/project/.env"]
```

Use an absolute `.env` path. Codex may launch subprocesses from a different
working directory.

The generated Codex config uses `command = "modal-mcp"`. Make sure that command
is on the PATH visible to Codex. From a source checkout, activate `.venv`, use a
globally installed wrapper, or edit the generated command to an absolute
executable path.

## Claude Desktop

Claude Desktop connects to a local server over SSE. Start the server first:

```bash
uv run modal-mcp run --env-file "$PWD/.env"
```

Preview the config entry:

```bash
uv run modal-mcp setup --install claude --dry-run
```

Install it:

```bash
uv run modal-mcp setup --install claude --yes
```

The installer writes a `mcpServers.modal-mcp` entry to the platform-specific
Claude Desktop config file, backs up the previous config, writes atomically, and
validates the result before reporting success.

Manual equivalent:

```json
{
  "mcpServers": {
    "modal-mcp": {
      "type": "sse",
      "url": "http://127.0.0.1:8765/mcp/sse"
    }
  }
}
```

Representative macOS path:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

## Other MCP Clients

Start the local server:

```bash
uv run modal-mcp run --env-file "$PWD/.env"
```

Use the Streamable HTTP endpoint when the client supports it:

```text
http://127.0.0.1:8765/mcp
```

Use the SSE endpoint when the client requires SSE:

```text
http://127.0.0.1:8765/mcp/sse
```

Keep these constraints in mind:

- The default bind is localhost only.
- `MODAL_MCP_ALLOWED_ORIGINS` must include the client origin for browser-based
  clients.
- `MODAL_MCP_ALLOWED_HOSTS` must include the Host header the client sends.
- For non-localhost exposure, configure a reverse proxy and set
  `MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE`.
