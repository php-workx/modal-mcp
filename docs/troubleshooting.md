# Troubleshooting

Start every investigation with:

```bash
uv run modal-mcp doctor --env-file .env
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | All checks passed. |
| `3` | Warnings only. Local use may still work, but the setup should be reviewed. |
| `1` | One or more required checks failed. |

## `.env` Is Missing

Symptom:

```text
.env file not found
```

Fix:

```bash
uv run modal-mcp setup --yes
uv run modal-mcp doctor --env-file .env
```

This fixes the missing `.env` and signing-key checks. `doctor` can still return
exit code `3` until Modal credentials are configured.

## Signing Key Is Missing Or Invalid

Symptoms:

```text
MODAL_MCP_SIGNING_KEYS is required
MODAL_MCP_SIGNING_KEY_FILE is missing
configured signing keys are malformed
```

Fix:

```bash
uv run modal-mcp setup --yes
uv run modal-mcp doctor --env-file .env
```

`--force` replaces the local `.env` and signing key. Review any local edits
before using it.

## Modal Credentials Are Missing

Symptoms:

```text
Modal credential source not found
Modal SDK auth failed
```

Fix options:

1. Use existing local CLI credentials in `~/.modal.toml`.
2. Prefer a dedicated Viewer-scoped service-user token and file-backed secrets.

File-backed token example:

Replace the placeholder values with a real non-production service-user token
before running the diagnostic command:

```bash
mkdir -p .secrets
printf '%s' '<modal-token-id>'     > .secrets/modal-token-id
printf '%s' '<modal-token-secret>' > .secrets/modal-token-secret
chmod 600 .secrets/*
printf '\nMODAL_TOKEN_ID_FILE=%s/.secrets/modal-token-id\n' "$PWD" >> .env
printf 'MODAL_TOKEN_SECRET_FILE=%s/.secrets/modal-token-secret\n' "$PWD" >> .env
uv run modal-mcp doctor --env-file .env
```

## Existing `~/.modal.toml` Triggers A Warning

Symptom:

```text
Existing Modal credentials found in ~/.modal.toml
```

Meaning:

The server can use those credentials, but they may belong to a personal editor
or admin account. For regular use, switch to a dedicated Viewer-scoped service
user token.

## Client Cannot Connect

Check that the server is running:

```bash
uv run modal-mcp run --env-file "$PWD/.env"
```

Then verify the client endpoint:

| Client transport | Endpoint |
| --- | --- |
| Streamable HTTP | `http://127.0.0.1:8765/mcp` |
| SSE | `http://127.0.0.1:8765/mcp/sse` |

If the request is rejected before tool handling, check:

- `MODAL_MCP_ALLOWED_ORIGINS`
- `MODAL_MCP_ALLOWED_HOSTS`
- browser or client proxy settings that alter `Origin` or `Host`

## Codex Install Fails

Common causes:

- `~/.codex` does not exist yet. Launch Codex once so it creates its config
  directory.
- `--env-file` is relative. Use an absolute path.
- `~/.codex/config.toml` contains an existing incompatible `modal-mcp` entry.

Preview before writing:

```bash
uv run modal-mcp setup --install codex --env-file "$PWD/.env" --dry-run
```

## Claude Desktop Install Fails

Common causes:

- Claude Desktop has not created its config directory yet.
- The config file is not valid JSON.
- A conflicting `modal-mcp` entry already exists.

Preview before writing:

```bash
uv run modal-mcp setup --install claude --dry-run
```

Claude Desktop connects to a running server. Start `uv run modal-mcp run
--env-file "$PWD/.env"` before testing the connection in Claude.

## Tools Are Missing From `tools/list`

Check enabled toolsets:

```bash
uv run modal-mcp doctor --env-file .env
```

Default toolsets:

```text
discovery,apps,containers,logs,volumes,sandboxes
```

`change` and `expert` are intentionally hidden and disabled in v1.

## Live Tests Are Skipped

This is expected unless `MODAL_MCP_LIVE=1` is set.

Use only non-production credentials:

```bash
MODAL_MCP_LIVE=1 MODAL_ENVIRONMENT=dev uv run pytest tests/integration/live -q
```
