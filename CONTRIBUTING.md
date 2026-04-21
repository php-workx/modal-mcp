# Contributing

## Governance posture

This project uses Apache-2.0 licensing with a DCO contribution model.
There is no CLA. Contributions are accepted through pull requests and
must be attributable to the person submitting them.

## Required sign-off

Every contribution must include a `Signed-off-by` line in the commit
message. The lowercase `signed-off-by` token must be present in the
final commit, for example:

```text
Signed-off-by: Your Name <you@example.com>
```

This is the standard Developer Certificate of Origin (`DCO`) signal. If
your commits are squashed or rebased, make sure the final commit still
contains the sign-off.

## Release expectation

Maintainers are expected to publish signed release tags and validate the
release through CI before distribution. Do not assume an unsigned tag or
artifact is a valid release.

## Practical rules

- keep changes focused
- include tests or verification where behavior changes
- avoid unrelated refactors in the same PR
- describe user-facing impact clearly in the PR body

## Local setup

After cloning, bootstrap a working local environment with the CLI:

```bash
uv sync --extra dev
uv run modal-mcp setup --yes
```

`setup --yes` generates two files (idempotent):

- `.env` — signing key path and allowed origins; **no Modal credentials**
- `.secrets/signing-key.txt` — HMAC signing key (mode `0600`)

Verify the installation before starting the server:

```bash
uv run modal-mcp doctor --env-file .env
```

`doctor` checks package imports, `.env` presence, signing key, origins,
read-only readiness, enabled toolsets, and Modal credential sources without
loading the full server settings. Exit code is `0` when all checks pass;
`3` when warnings exist but no check fails (partial-ready); `1` when at least
one check fails.

### Credential safety

`modal-mcp setup` does **not** create or configure a Modal service-user token.
You must supply Modal credentials separately:

- **Recommended:** create a dedicated service-user with Viewer permissions in a
  non-production Modal workspace.  Store the token in `.secrets/modal-token-id`
  and `.secrets/modal-token-secret` (mode `0600`), then set
  `MODAL_TOKEN_ID_FILE` / `MODAL_TOKEN_SECRET_FILE` at runtime.
- **Fallback:** `~/.modal.toml` is picked up automatically if present.
  `doctor` warns when that file exists because it often contains personal or
  admin credentials with broader permissions than the read-only server requires.

Never add `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, or `MODAL_ENVIRONMENT` to
the `.env` file.  The generated `.env` enforces this by design.

### Agent config

Print the config block for your agent client to review before writing any files:

```bash
uv run modal-mcp print-agent-config --target codex --env-file "$PWD/.env"
uv run modal-mcp print-agent-config --target claude --env-file "$PWD/.env"
```

To install automatically into Codex CLI or Claude Desktop, use `--install`:

```bash
# Codex CLI — preview then write the [mcp_servers.modal-mcp] entry
uv run modal-mcp setup --install codex --env-file "$PWD/.env" --dry-run
uv run modal-mcp setup --install codex --env-file "$PWD/.env" --yes

# Claude Desktop — preview then write the mcpServers.modal-mcp SSE entry
uv run modal-mcp setup --install claude --dry-run
uv run modal-mcp setup --install claude --yes
```

Both install commands back up the existing config, write atomically, validate
the round-trip, and are idempotent (no-op when the entry already matches).
`--install claude` does not require `--env-file` because Claude Desktop
connects over SSE to a separately-started server.

## Local quality gates

Install the local command runners before using the validation targets:

```bash
brew install just shellcheck actionlint betterleaks semgrep
```

On Linux, install the same tools with your package manager or the upstream
release packages.

Install the local tools and git hooks once per worktree:

```bash
just setup
```

The checked-in hooks delegate to the same `just` targets used by maintainers:

```bash
just pre-commit   # format, lint, workflow/hook lint, type check, schema drift, fast tests
just pre-push     # pre-commit gate, full tests, vulnerability/security scans
just check        # full local quality gate (alias for pre-push)
```

Use targeted commands while iterating:

```bash
just format
just lint
just type-check
just test-fast
just vuln
just uv-audit
just betterleaks
just semgrep
```

Local secret scanning uses Betterleaks through the `just` targets. CI runs
TruffleHog for repository secret scanning.
