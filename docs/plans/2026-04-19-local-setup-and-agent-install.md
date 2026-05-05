# Local Setup and Agent Install Implementation Plan

**Date:** 2026-04-19
**Scope:** Local developer setup workflow, `modal-mcp doctor` diagnostics, and
agent-client install commands for Codex CLI and Claude Desktop.

---

## Goal

Make the full local setup path reliable and documented: from cloning the repo
through running `doctor`, supplying Modal credentials, and registering
`modal-mcp` with an agent client (Codex CLI or Claude Desktop).

This plan covers implementation that is already shipped; it is the tracking
artifact for the local-setup and agent-install scope.

---

## Non-Goals

- Hosted OAuth or `/session/create`.
- Request-scoped adapter resolution.
- Enabling mutating `change` or `expert` tools by default.
- Approval UX or mutation execution.
- Helm, Kubernetes, or remote deployment packaging.

---

## Implemented Scope

### 1. `modal-mcp setup --yes`

Generates two files (idempotent — existing files are preserved):

| File                       | Mode   | Content                                                    |
|----------------------------|--------|------------------------------------------------------------|
| `.env`                     | `0600` | `MODAL_MCP_SIGNING_KEY_FILE` + `MODAL_MCP_ALLOWED_ORIGINS` |
| `.secrets/signing-key.txt` | `0600` | `kid:hex` HMAC signing key                                 |

The generated `.env` intentionally omits all Modal credential variables
(`MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `MODAL_ENVIRONMENT`).

`setup` does **not** automatically create a scoped Modal service-user token.
The user must create the service-user and token through the Modal dashboard
or CLI (`modal token new`) and supply credentials separately.

**Files:** `src/modal_mcp/setup.py`, `src/modal_mcp/setup_files.py`

### 2. `modal-mcp doctor`

Partial-loading diagnostic checks that run *before* full `Settings`
validation is required.  Checks (in order):

1. Package imports: `modal_mcp`, `modal`, `fastmcp`, `uvicorn`
2. `.env` file presence
3. Signing key (`MODAL_MCP_SIGNING_KEYS` or `MODAL_MCP_SIGNING_KEY_FILE`)
4. Allowed origins (`MODAL_MCP_ALLOWED_ORIGINS`)
5. Read-only readiness (`MODAL_MCP_READ_ONLY`)
6. Enabled toolsets (`MODAL_MCP_ENABLED_TOOLSETS` — warns on `change`/`expert`)
7. Modal credential probe (env vars, `.env` file, file-backed paths,
   `~/.modal.toml`)
8. SDK auth health (when credentials are present)
9. Modal CLI presence

**Exit code contract:**

| Code | Meaning                                       |
|------|-----------------------------------------------|
| `0`  | All checks pass                               |
| `3`  | Warnings present, no failures (partial-ready) |
| `1`  | At least one check fails                      |

**Files:** `src/modal_mcp/doctor.py`

### 3. `modal-mcp setup --install codex`

Installs the `[mcp_servers.modal-mcp]` entry into `~/.codex/config.toml`
with atomic backup, round-trip validation, and idempotency.

- Requires `--env-file` to be an absolute path (Codex embeds it in its
  config for subprocess launch at any working directory).
- `--dry-run` previews the change without writing.
- Refuses symlinked configs, unparseable TOML, or conflicting entries.

**Files:** `src/modal_mcp/agent_config.py`,
`src/modal_mcp/agent_targets/codex.py`

### 4. `modal-mcp setup --install claude`

Installs the `mcpServers.modal-mcp` SSE entry into `claude_desktop_config.json`
with atomic backup, round-trip validation, and idempotency.

- Does **not** require `--env-file` — Claude Desktop connects over SSE to
  a separately-started server; the env file is not embedded in the config
  entry.
- `--dry-run` previews the change without writing.
- Refuses symlinked configs, unparseable JSON, or conflicting entries.

**Files:** `src/modal_mcp/agent_config.py`,
`src/modal_mcp/agent_targets/claude.py`

### 5. `modal-mcp print-agent-config`

Prints the config snippet for the specified target without writing any
files.

```bash
modal-mcp print-agent-config --target codex   # TOML block for Codex CLI
modal-mcp print-agent-config --target claude  # JSON block for Claude Desktop
```

**Files:** `src/modal_mcp/agent_config.py`

---

## Credential Safety

- `modal-mcp setup` never reads or writes Modal credentials.
- `doctor` warns when `~/.modal.toml` is present (likely personal/admin
  credentials with broader permissions than the read-only server requires).
- Recommended path: dedicated Modal service-user with Viewer permissions in a
  non-production workspace; token stored in file-backed secrets.

---

## Local Validation Gate

Install tooling once per worktree:

```bash
brew install just shellcheck actionlint betterleaks semgrep
just setup
```

Run quality gates:

```bash
just pre-commit   # format, lint, workflow/hook lint, type check, schema drift, fast tests
just pre-push     # pre-commit gate, full tests, vulnerability/security scans
```

Local secret scanning uses **Betterleaks** through the `just` targets.
CI uses **TruffleHog** for repository secret scanning.

---

## Related Plans

- `docs/plans/2026-04-17-local-read-only-readiness.md` — server launch path,
  ASGI smoke coverage, and read-only toolset gates that this plan builds on.
