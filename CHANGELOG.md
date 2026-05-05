# Changelog

All notable changes to this project will be documented here.

This project uses human-readable release notes for users. Internal ticket names,
review IDs, and implementation-only changes should stay out of this file unless
they affect installation, operation, security, or compatibility.

## Unreleased

- Add a local read-only Modal MCP server for inspecting Modal workspaces,
  environments, apps, deployments, logs, containers, volumes, and sandboxes.
- Add setup and diagnostic commands that generate local server configuration,
  preserve credentials outside `.env`, and check readiness before startup.
- Add Codex CLI and Claude Desktop installation helpers with dry-run previews,
  backups, atomic writes, and validation.
- Add default read-only safety controls, toolset allowlists, Origin and Host
  guards, audit logging, and output redaction.
- Add local development guardrails for tests, formatting, linting, type checks,
  shell checks, workflow checks, dependency audit, and secret scanning.
