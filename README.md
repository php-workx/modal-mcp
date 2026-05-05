# Modal MCP Server

![Status: local read-only beta](https://img.shields.io/badge/status-local%20read--only%20beta-0f766e)
![Python: 3.12+](https://img.shields.io/badge/python-3.12%2B-3776ab)
![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-3b82f6)

Local, read-only Modal context for coding agents.

Modal MCP lets Codex, Claude Desktop, and other MCP clients inspect Modal apps,
deployments, logs, containers, volumes, and sandboxes without granting mutation
tools. It runs on your machine, uses your local Modal credentials, and exposes
only read-oriented toolsets by default.

## Why Use It

- Let agents inspect live Modal state instead of asking you to paste CLI output.
- Debug deployments, startup failures, and recent logs from the same chat.
- Keep Modal credentials local to your machine.
- Start with read-only behavior; destructive tools are disabled in v1.

## Project Status

v1 is a local read-only beta.

| Area | Status |
| --- | --- |
| Local MCP server | Supported |
| Claude Code install | Manual config in `~/.claude/settings.json` |
| Codex CLI install | Supported by `modal-mcp setup --install codex` |
| Claude Desktop install | Supported by `modal-mcp setup --install claude` |
| Read-only Modal tools | Supported |
| Mutating Modal tools | Disabled stubs only |
| Hosted OAuth / multi-tenant service | Not part of v1 |
| Helm / Kubernetes packaging | Deferred |

## Installation

### One-liner (recommended)

```bash
# Pin to a release tag for supply-chain safety
curl -fsSL https://raw.githubusercontent.com/php-workx/modal-mcp/refs/tags/v0.1.0/install.sh -o install.sh
# Verify checksum against release notes, then execute:
sh install.sh
```

This script auto-detects your Python toolchain and installs `modal-mcp` with the best available method:

| Toolchain | Command used | Notes |
|-----------|-------------|-------|
| **uv** (preferred) | `uv tool install modal-mcp` | Fast, isolated, no virtualenv needed |
| **pipx** | `pipx install modal-mcp` | Good isolation, widely available |
| **pip** (fallback) | `pip install --user modal-mcp` | Always works, requires PATH setup |

### From source

```bash
git clone https://github.com/php-workx/modal-mcp.git
cd modal-mcp
uv sync --extra dev
uv run modal-mcp --help
```

### PyPI

```bash
uv tool install modal-mcp
# or
pipx install modal-mcp
# or
pip install --user modal-mcp
```

## Quickstart

Install from source and generate local server settings:

```bash
uv sync --extra dev
uv run modal-mcp setup --yes
```

`modal-mcp setup --yes` creates `.env` and `.secrets/signing-key.txt` when
missing.  If an `.env` already exists, missing `MODAL_MCP_*` keys are merged
into it; existing content (including Modal tokens and `MODAL_ENVIRONMENT`)
is left unchanged.

| File | Mode | Contains |
| --- | --- | --- |
| `.env` | `0600` | Local server settings and signing-key path |
| `.secrets/signing-key.txt` | `0600` | HMAC signing key for server internals |

The generated `.env` intentionally does not contain Modal tokens or
`MODAL_ENVIRONMENT` unless they are already present in an existing file.

If this machine already has Modal CLI credentials in `~/.modal.toml`, you can
verify and start the server immediately:

```bash
uv run modal-mcp doctor --env-file .env
uv run modal-mcp run --env-file .env
```

### Add Modal Credentials

For a quick local evaluation, existing Modal CLI credentials in `~/.modal.toml`
are used automatically. `modal-mcp doctor` warns when it finds them because they
often belong to a personal editor or admin account.

For regular use, create a dedicated Modal service-user token with Viewer access
and store it in files. Replace the placeholder values before running the
diagnostic or server commands:

```bash
mkdir -p .secrets
printf '%s' '<modal-token-id>'     > .secrets/modal-token-id
printf '%s' '<modal-token-secret>' > .secrets/modal-token-secret
chmod 600 .secrets/*
printf '\nMODAL_TOKEN_ID_FILE=%s/.secrets/modal-token-id\n' "$PWD" >> .env
printf 'MODAL_TOKEN_SECRET_FILE=%s/.secrets/modal-token-secret\n' "$PWD" >> .env
```

Run the diagnostics again after adding credentials:

```bash
uv run modal-mcp doctor --env-file .env
uv run modal-mcp run --env-file .env
```

## What Agents Can Ask

Try prompts like these after connecting your MCP client:

- "Which Modal workspaces and environments can this token see?"
- "List the apps in the current environment and summarize deployment status."
- "Show recent logs for this app and group likely startup failures."
- "Compare these two deployment versions and tell me what changed."
- "List running sandboxes and containers so I can spot stale resources."
- "Inspect this volume path and read a small text file from it."

See [docs/examples.md](docs/examples.md) for longer workflows.

## Toolsets

| Toolset | Tools | Helps agents answer |
| --- | --- | --- |
| `discovery` | `modal_discovery_server_info`, `modal_whoami`, `modal_list_workspaces`, `modal_list_environments`, `modal_get_environment` | Who am I authenticated as, what environments exist, and what server features are enabled? |
| `apps` | `modal_list_apps`, `modal_get_app`, `modal_list_app_deployments` | What apps exist and what versions are deployed? |
| `logs` | `modal_get_app_logs`, `modal_search_logs`, `modal_summarize_failures`, `modal_compare_deployments`, `modal_diagnose_app_startup` | What happened recently and what failures look most likely? |
| `containers` | `modal_list_containers`, `modal_get_container`, `modal_get_container_logs` | Which containers exist and what are they reporting? |
| `volumes` | `modal_list_volumes`, `modal_ls_volume`, `modal_read_volume_text`, `modal_stat_volume_path` | What volume paths exist and what small text content can be inspected? |
| `sandboxes` | `modal_list_sandboxes`, `modal_get_sandbox`, `modal_get_sandbox_stdio` | What sandboxes exist and what stdio did they produce? |

The `change` and `expert` toolsets are registered only as disabled stubs so
policy code can block them consistently. They are hidden from default
`tools/list`.

Full catalog: [docs/tools.md](docs/tools.md).

## Connect An Agent

### Codex CLI

Codex launches `modal-mcp` as a subprocess over stdio:

```bash
uv run modal-mcp setup --install codex --env-file "$PWD/.env" --dry-run
uv run modal-mcp setup --install codex --env-file "$PWD/.env" --yes
```

### Claude Desktop

Claude Desktop connects to a running local server:

```bash
uv run modal-mcp run --env-file "$PWD/.env"
uv run modal-mcp setup --install claude --dry-run
uv run modal-mcp setup --install claude --yes
```

### Other MCP Clients

Point Streamable HTTP clients at:

```text
http://127.0.0.1:8765/mcp
```

For clients that still expect SSE:

```text
http://127.0.0.1:8765/mcp/sse
```

More details: [docs/clients.md](docs/clients.md).

## Safety Model

- Local by default: the server binds to `127.0.0.1:8765` unless configured
  otherwise.
- Read-only by default: `MODAL_MCP_READ_ONLY=true`.
- Toolsets are allowlisted by `MODAL_MCP_ENABLED_TOOLSETS`.
- Origin and Host allowlists run before MCP request handling.
- Known secrets are redacted before JSON responses and audit logs leave the
  server.
- Raw Modal tokens should not be placed in `.env`; prefer file-backed secrets.

Volume text, logs, and sandbox stdio can still contain application-sensitive
data. Use a non-production workspace or a dedicated Viewer-scoped service user
when evaluating the server.

Security details: [docs/threat-model.md](docs/threat-model.md).

## Configuration

Required for self-hosted startup:

| Variable | Purpose |
| --- | --- |
| `MODAL_MCP_ALLOWED_ORIGINS` | Browser/client origins allowed to call the MCP server |
| `MODAL_MCP_SIGNING_KEYS` or `MODAL_MCP_SIGNING_KEY_FILE` | HMAC signing keys for internal refs, cursors, and approval tokens |
| Modal credentials | Either `~/.modal.toml`, `MODAL_TOKEN_ID` plus `MODAL_TOKEN_SECRET`, or their `_FILE` variants |

Recommended defaults:

```bash
MODAL_MCP_READ_ONLY=true
MODAL_MCP_ENABLED_TOOLSETS=discovery,apps,containers,logs,volumes,sandboxes
MODAL_MCP_AUDIT_LOG=stdout
MODAL_MCP_HTTP_BIND=127.0.0.1:8765
```

Full deployment guide: [docs/self-hosting.md](docs/self-hosting.md).

## Troubleshooting

Start with:

```bash
uv run modal-mcp doctor --env-file .env
```

`doctor` checks package imports, `.env`, signing keys, allowed origins,
read-only settings, enabled toolsets, Modal credential source, Modal SDK auth,
and Modal CLI availability.

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Ready |
| `3` | Warnings only; local use may still work |
| `1` | At least one required check failed |

Common fixes: [docs/troubleshooting.md](docs/troubleshooting.md).

## Development

Install local command runners:

```bash
brew install just shellcheck actionlint betterleaks semgrep
```

Run the local gates:

```bash
just setup
just pre-commit
just pre-push
just check
just uv-audit
```

Live Modal tests are opt-in and should use non-production credentials:

```bash
MODAL_MCP_LIVE=1 MODAL_ENVIRONMENT=dev uv run pytest tests/integration/live -q
```

## Documentation

- [Tool catalog](docs/tools.md)
- [Example agent workflows](docs/examples.md)
- [Client setup](docs/clients.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture](docs/architecture.md)
- [Self-hosting guide](docs/self-hosting.md)
- [Threat model](docs/threat-model.md)
- [Security policy](SECURITY.md)
- [Contributing guide](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
