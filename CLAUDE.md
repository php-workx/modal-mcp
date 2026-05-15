# CLAUDE.md

Read `AGENTS.md` first. It is the durable repo instruction file.

Preserve the repo’s read-only Modal posture unless the task explicitly says
otherwise.

## Implementation Skills

  When executing tasks as subagents:
  - Invoke `agent-skills:context-engineering` at task start (context hierarchy, <2k lines/task)
  - Invoke `agent-skills:source-driven-development` before writing any code touching external libraries/APIs
  - Follow `test-driven-development` (superpowers) for all implementation — RED then GREEN then REFACTOR


## Agent skills

### Issue tracker

Issues tracked via `epos` (local CLI for epics, tasks, issues, and tickets). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — one `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.
