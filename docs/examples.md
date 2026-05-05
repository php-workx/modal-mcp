# Example Agent Workflows

These examples show the kind of work Modal MCP is meant to support. They are
written as prompts you can give to an MCP-aware coding agent after the server is
connected.

## Confirm Access

Prompt:

```text
Use Modal MCP to tell me which Modal account, workspaces, and environments are
visible. Do not read logs or volume contents yet.
```

Expected tool path:

1. `modal_discovery_server_info`
2. `modal_whoami`
3. `modal_list_workspaces`
4. `modal_list_environments`

Useful when you want to verify that the agent is connected to the right
workspace before it inspects application data.

## Review Deployment State

Prompt:

```text
List Modal apps in this environment. For each app, summarize the latest
deployment versions and call out anything that looks stale or missing.
```

Expected tool path:

1. `modal_list_apps`
2. `modal_list_app_deployments` for relevant apps
3. `modal_get_app` when the agent needs details for one app

Useful when you want a quick inventory without running `modal app list` and
copying results into chat.

## Triage A Startup Failure

Prompt:

```text
Investigate why this Modal app is not starting cleanly. Use recent logs first,
then summarize likely failure signatures and the next command I should run.
```

Expected tool path:

1. `modal_get_app`
2. `modal_get_app_logs`
3. `modal_summarize_failures`
4. `modal_diagnose_app_startup`

The agent should quote compact evidence, not dump every log line.

## Search Logs For A Known Error

Prompt:

```text
Search recent Modal logs for "CUDA out of memory" and group the results by
function or container when possible.
```

Expected tool path:

1. `modal_search_logs`
2. `modal_get_container_logs` when a container reference is relevant

Useful when you know the symptom and want the agent to collect enough context
to suggest a fix.

## Inspect Volume Metadata Before Reading Content

Prompt:

```text
List the root of this Modal volume. If you find a small text file that looks
like a run summary, read at most 64 KiB and summarize it.
```

Expected tool path:

1. `modal_ls_volume`
2. `modal_stat_volume_path`
3. `modal_read_volume_text` with a bounded `max_bytes`

Volume content can contain sensitive application data. Prefer non-production
tokens when trying this workflow.

## Inspect Sandboxes

Prompt:

```text
List running sandboxes and summarize recent stdio for any sandbox that looks
failed or stuck.
```

Expected tool path:

1. `modal_list_sandboxes`
2. `modal_get_sandbox`
3. `modal_get_sandbox_stdio`

This is useful for debugging sandbox-based automation without switching back to
manual CLI inspection.
