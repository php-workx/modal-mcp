# Tool Catalog

Modal MCP v1 exposes read-oriented Modal tools. The server registers disabled
mutation stubs for future compatibility, but default local use only enables:

```text
discovery,apps,containers,logs,volumes,sandboxes
```

All enabled tools return structured envelopes so agents can distinguish
successful data from not-found and policy errors.

## Discovery

| Tool | Purpose | Common use |
| --- | --- | --- |
| `modal_discovery_server_info` | Returns server version, enabled toolsets, and capability metadata. | Confirm the MCP server is connected and read-only toolsets are enabled. |
| `modal_whoami` | Returns the authenticated Modal identity/workspace context. | Check which account or service user the agent is using. |
| `modal_list_workspaces` | Lists visible Modal workspaces. | Confirm workspace access before querying apps. |
| `modal_list_environments` | Lists visible Modal environments. | Let users switch context without reconfiguring the server. |
| `modal_get_environment` | Returns details for one environment. | Inspect one environment before app or resource queries. |

## Apps And Deployments

| Tool | Purpose | Common use |
| --- | --- | --- |
| `modal_list_apps` | Lists apps for an optional environment. | Inventory apps visible to the configured credentials. |
| `modal_get_app` | Returns one app by reference. | Inspect status and metadata for a known app. |
| `modal_list_app_deployments` | Lists deployment versions for an app. | Review recent deploy history and choose versions to compare. |

## Logs And Diagnostics

| Tool | Purpose | Common use |
| --- | --- | --- |
| `modal_get_app_logs` | Reads bounded app logs with optional time and source filters. | Investigate recent failures or startup behavior. |
| `modal_search_logs` | Searches bounded app logs for text. | Find error signatures, task IDs, or function-call IDs. |
| `modal_summarize_failures` | Groups recent log errors into compact failure signatures. | Give an agent a faster path from raw logs to likely causes. |
| `modal_compare_deployments` | Compares two deployment version references with v1 summary fields. | Check whether both versions exist and get a compact comparison payload. |
| `modal_diagnose_app_startup` | Produces a small startup diagnosis from recent logs. | Triage startup problems before asking for deeper log inspection. |

## Containers

| Tool | Purpose | Common use |
| --- | --- | --- |
| `modal_list_containers` | Lists containers, optionally scoped by environment and app. | Find active or stale containers. |
| `modal_get_container` | Returns one container by reference. | Inspect a container selected from a list. |
| `modal_get_container_logs` | Reads bounded logs for one container. | Debug a specific container without scanning all app logs. |

## Volumes

| Tool | Purpose | Common use |
| --- | --- | --- |
| `modal_list_volumes` | Lists volumes in an optional environment. | Inventory data stores visible to the token. |
| `modal_ls_volume` | Lists entries under a volume path. | Inspect directory structure before reading a file. |
| `modal_read_volume_text` | Reads a bounded UTF-8 text file from a volume. | Let an agent inspect small config or output files. |
| `modal_stat_volume_path` | Returns metadata for one volume path. | Check file existence and metadata without reading content. |

`modal_read_volume_text` is read-only but can expose application data. Use
Viewer-scoped credentials and non-production workspaces for evaluation.

## Sandboxes

| Tool | Purpose | Common use |
| --- | --- | --- |
| `modal_list_sandboxes` | Lists sandboxes, optionally including finished sandboxes. | Identify running or recently finished sandbox work. |
| `modal_get_sandbox` | Returns one sandbox by reference. | Inspect sandbox status and metadata. |
| `modal_get_sandbox_stdio` | Reads bounded sandbox stdio. | Debug sandbox output without asking the user to copy logs. |

Sandbox stdio may contain application-sensitive output. Treat it like logs.

## Disabled V1 Toolsets

| Toolset | Registered tools | Status |
| --- | --- | --- |
| `change` | `modal_stop_app`, `modal_rollback_app`, `modal_stop_container`, `modal_terminate_sandbox` | Disabled by default and blocked by read-only policy. |
| `expert` | `modal_expert_search`, `modal_expert_execute` | Disabled in v1. |

These tools are present so policy, approval, and future tool-list behavior can
be tested against stable names. They are not usable in the v1 local read-only
server.
