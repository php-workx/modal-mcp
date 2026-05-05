# AGENTS.md

`AGENTS.md` is the durable instruction file for this repo.

## What This Repo Is

`modal-mcp` is a local, read-oriented MCP server for inspecting Modal state.
The core product boundary is that credentials stay local and the default tool
surface is read-only.

## Hard Rules

- Preserve the read-only default posture unless a task explicitly asks for
  mutating behavior.
- Never check real Modal credentials or secrets into the repo.
- Prefer the repo’s `uv`-managed Python environment over ambient Python.
- Keep local-file secret handling and `.env`-driven setup intact.
- **Never merge pull requests or create releases without explicit user approval.**
  Agents may prepare, review, and push changes, but the human must trigger
  merges, releases, and any action that modifies the default branch.

## Common Commands

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run modal-mcp doctor --env-file .env
uv run modal-mcp run --env-file .env
```

Optional stricter checks if needed:

```bash
uv run mypy src
uv run pip-audit
```

## Common Workflows

### 1. Server or tool changes

1. Run tests and lint through `uv`.
2. Preserve the read-oriented tool contract unless the task explicitly changes
   that product decision.
3. Keep setup and doctor flows working for local users.

### 2. Credential or setup changes

1. Do not replace file-based secret handling with checked-in secrets.
2. Keep `.env` and `.secrets/` semantics local-only.
3. If setup behavior changes, update the README examples too.

## References

- `README.md` — product boundaries and setup flow
- `pyproject.toml` — runtime and dev-tool entry points
