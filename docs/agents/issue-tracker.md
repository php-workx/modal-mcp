# Issue Tracker

Issues for this repo are managed via `epos` — a local CLI tool for epics, tasks, issues, and tickets.

## How skills should interact with it

- **Create an issue:** `epos` (exact subcommand TBD — run `epos --help` to discover)
- **List issues:** `epos` (exact subcommand TBD)
- **Update issue status/labels:** `epos` (exact subcommand TBD)

Before performing any issue operation, run `epos --help` (or `epos <subcommand> --help`) to confirm the correct flags and subcommand names. Do not assume GitHub CLI (`gh`) or GitLab CLI (`glab`) conventions.

## Notes

- `epos` is a local CLI; no external API credentials are required.
- There is no remote issue tracker — all issue state lives locally.
