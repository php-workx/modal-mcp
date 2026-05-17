# ADR 0001: Claude Code adapter delegates to `claude mcp add-json`

**Status:** Accepted  
**Date:** 2026-05-17

## Context

All other agent target adapters (codex, claude_desktop) write config files
directly (JSON merge / TOML merge). For Claude Code the natural target would
be `~/.claude.json`, but that file is a large opaque store managed by Claude
Code itself, containing per-project MCP entries, session state, and history.
Writing into it directly risks stomping unrelated data and breaks when
Claude Code changes its internal schema.

Claude Code exposes `claude mcp add-json <name> '<json>' --scope <scope>`
as the canonical write mechanism, handling backup and conflict resolution
internally.

`~/.claude/settings.json` (a different file) stores settings such as hooks,
theme, and permissions. It does NOT store `mcpServers`; writing there is
silently ignored by Claude Code.

## Decision

The `claude_code` adapter shells out to `claude mcp add-json` rather than
writing `~/.claude.json` directly. Idempotency is checked by reading
`~/.claude.json` for structural comparison before any subprocess call (for
user scope only); writing is delegated to the CLI.

## Consequences

- Install requires `claude` CLI on PATH; adapter errors with a clear message
  when absent.
- Backup behaviour is whatever Claude Code's CLI provides (not our `.bak.*`
  pattern).
- Future schema changes to `~/.claude.json` are Claude Code's responsibility.
- `print-agent-config --target claude-code` prints the `claude mcp add-json`
  command rather than a raw JSON snippet, matching what install actually runs.
- Idempotency check via file read is only performed for `--scope user`; other
  scopes always delegate to the CLI.
