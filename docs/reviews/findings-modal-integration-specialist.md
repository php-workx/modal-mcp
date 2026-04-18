# Review — `modal-mcp_v2.md`, Modal integration specialist

**Bottom line.** The plan's Modal story is a single load-bearing claim — "`modal.client.stub` exposes the RPCs we need under the names we listed, with shapes our normaliser can consume, and they are stable because the CLI uses them too" — and it is almost entirely unverified. The specific RPC names in §6.2 (`AppList`, `AppStop`, `AppRollback`, `AppGetHistory`, `ContainerList`, `ContainerStop`, `ContainerLogs`, `VolumeListFiles`, `VolumeGetFile`, `WorkspaceList`) are educated guesses that need to be checked against `modal_proto/api.proto` before a single tool is written; several are probably wrong in name, several more are wrong in shape (pagination, filtering, streaming), and at least two of the semi-public module entry points (`modal._logs.fetch_logs` with `LogsFilters`, `modal._VolumeManager.list`, `modal.sandboxes.list` as a pure async generator) are cited the way a reader of Modal docs would guess they work, not the way they actually work in the current package. The §3.2 stability argument ("CLI and `_grpc_client` break together") is *structurally* true but *operationally misleading*: the CLI adds a meaningful layer of pagination loops, path normalisation, error translation, chunked reads, and retry, none of which the plan acknowledges, all of which the adapter now owns. The §9.1 drift probe as described would not detect the realistic drift modes at all. The plan is coherent about the *wrapping* of Modal (envelopes, refs, policy, approvals), but the Modal integration itself is specified at the level of a table of intent, not a table of verified calls. There are at least eight items here that will cost a day or more of rework each if discovered during v0.

---

### F1. Named gRPC RPCs in §6.2 are unverified and several are almost certainly wrong

**Severity:** Blocker

**Why it matters:** The entire in-process adapter architecture rests on this table being implementable as written. If `AppList`/`AppStop`/`AppRollback`/`AppGetHistory`/`ContainerList`/`ContainerStop`/`ContainerLogs`/`VolumeListFiles`/`VolumeGetFile`/`WorkspaceList` are not the actual method names on `modal.client.Client.stub` (or if they exist but take a request message the spec's normaliser code is not written to handle), every v0 day between "write the adapter" and "discover the real names" is wasted. Modal's gRPC surface historically uses names like `AppList`, `AppStop`, `AppDeploy`, `AppDeploySingleObject`, `AppGetByDeploymentName`, `AppRollback`, `AppGetHistory` for some, but also names like `ContainerExec`, `TaskLogs` (not `ContainerLogs`), `VolumeListFiles2` (note the `2` — the first version was replaced), `VolumeGetFile2`, and workspace operations typically live under `WorkspaceGet` / `WorkspaceCurrent`, not `WorkspaceList`. Cannot be verified without reading the current `modal_proto/api.proto`; §13 claims verification by "direct inspection of `modal-labs/modal-client`" but the inspection evidence cited is only *module paths*, never RPC method names.

**Evidence from the plan:** v2:§6.2 lines 1019–1045, the entire "Implementation path" column. The names appear only in this table and nowhere else in the document. §13 item 1 (L1693–1725) is the "coverage evidence" paragraph — it names modules, never individual gRPC methods. §16.1 decision 6 (L1841–1847) re-asserts the claim without evidence.

**Recommended change:** Before v0 begins, replace the §6.2 table with a version where each "Implementation path" cell cites the exact line in `modal_proto/api.proto` (upstream) or the exact `client.stub.<Method>` reference found in `modal.cli.*` source. Add an appendix §6.2a "Verified RPC inventory" listing: (a) the exact method name as it appears on the stub object, (b) the request message type, (c) the response message type, (d) which CLI command uses it, (e) whether it is unary or streaming. Until that appendix exists, the §6.2 table should be marked `UNVERIFIED` at the top and not used as an implementation contract. Specific questions that must be answered: does `ContainerLogs` exist, or is it `TaskLogs`? Is `VolumeListFiles` the current name or has it been superseded by a `2` variant? Is `WorkspaceList` real, or does workspace enumeration actually go through `MODAL_CONFIG` profile parsing + a single `WorkspaceGet` probe?

---

### F2. `list_workspaces` via a `WorkspaceList` RPC is almost certainly not how Modal workspaces work

**Severity:** Critical

**Why it matters:** Modal's concept of "workspace" is a client-side construct stored in `~/.modal.toml` profiles. A single Modal token authenticates to exactly one workspace; there is no server-side RPC that returns "workspaces this token can see" because a token cannot see more than one workspace. If the adapter tries to call a `WorkspaceList` RPC on the stub, the most likely outcomes are (a) `AttributeError: 'ModalClientStub' object has no attribute 'WorkspaceList'`, or (b) the RPC exists but returns exactly one entry (the calling workspace) which makes the entire `modal_list_workspaces` tool a thin wrapper over `modal.config.Config.get("workspace")`. Either way, the v0 implementation of this tool discovers it on day 1 and has to either delete the tool, re-scope it as "list profiles in `~/.modal.toml`", or chain `WorkspaceGet` with a full profile list from config.

**Evidence from the plan:** v2:§6.2 L1023 "`list_workspaces` | `modal._grpc_client` — `WorkspaceList` RPC via `client.stub`". The tool is also listed in §5.1 L541 as `modal_list_workspaces` in the `discovery` toolset. §6.1 L947 declares `list_workspaces(self, cursor: Cursor | None) -> Page[Workspace]` with cursor pagination — implying the author thought this was a paged server-side listing.

**Recommended change:** Re-specify `list_workspaces` as: "read local Modal profile config via `modal.config.Config` (or equivalent), return the set of profiles locally known to this process. If hosted mode is active and profiles come from operator-provided tokens, return the single authenticated workspace. This tool does NOT make a Modal RPC call; it is a local introspection helper." Drop `cursor` from the signature. Add a note in §13 clarifying that Modal tokens are workspace-scoped and cross-workspace listing is impossible by design.

---

### F3. `modal._logs.fetch_logs(app_id, filters=LogsFilters(...))` and `tail_logs(app_id=...)` signatures are cited as fact but not verified

**Severity:** Critical

**Why it matters:** `modal._logs` does exist as a module in recent Modal versions, but the spec cites a specific signature (`fetch_logs(app_id=..., filters=LogsFilters(...))`) and a specific companion type (`LogsFilters`) and a specific iteration shape (`async for batch in tail_logs(app_id=...)`). Unknown whether the public function is called `fetch_logs` or `get_app_logs`; whether it takes `app_id` or `app`; whether `LogsFilters` is the correct type name or whether it's `LogsReaderFilters` / `LogsFilter` / `LogsQuery`; or whether `tail_logs` yields `LogEntry` objects, batches of entries, `LogChunk` messages, or raw proto `TaskLogsResponse` messages that need unpacking. If any of these are wrong — and the likelihood that all four are exactly right is low — then §6.2's `get_app_logs` and `tail_app_logs` rows break on v0 day 1, the `get_container_logs` row (which is specified as a different RPC entirely) needs to be reconciled with what `fetch_logs` can actually filter on, and the `modal_summarize_failures` / `modal_compare_deployments` / `modal_diagnose_app_startup` tools that depend on structured log entries have to re-derive their data shape.

**Evidence from the plan:** v2:§6.2 L1029–1030 (`fetch_logs(app_id=…, filters=LogsFilters(…))` and `async for batch in modal._logs.tail_logs(app_id=…)`), §2.3 L111, §3.1 L137, §13 L1710–1711. None of these cite a file:line in the Modal source.

**Recommended change:** Before any adapter code is written, verify by reading `modal/_logs.py` in the pinned Modal version: (a) the actual function names, (b) the actual argument names and types, (c) whether filters are a dedicated dataclass or kwargs, (d) whether the return type is `LogEntry` objects or raw protos, (e) what `tail_logs` yields (one entry per iteration vs. batches), (f) whether `tail_logs` has a built-in reconnect on disconnect or whether the caller must handle it, (g) whether it dedupes entries on reconnect or emits duplicates. Replace §6.2 L1029–1030 with the verified signature. If the return type is raw proto, add a §6.2 row "normalise `TaskLogsResponse` → `LogEntry`" and a fixture file.

---

### F4. `tail_logs` reconnect, dedup, and backpressure semantics are undefined — and "no sidecar needed" assumes they Just Work

**Severity:** Major

**Why it matters:** The plan promotes log streaming from "v2 with sidecar" to "v1 with `async for batch in tail_logs(...)` + `ctx.report_progress`" (§16.1 decision 3). That compresses the timeline but only if `tail_logs` is actually a well-behaved long-running async generator. Real streaming log tails against a Modal control plane under load involve: (a) the gRPC server closing the stream on idle, (b) partial batches when the server buffer flushes mid-log-line, (c) reconnect requires a "since cursor" to avoid gaps or duplicates, (d) backpressure — if the MCP client reads slowly, the `ctx.report_progress` calls may block the generator and the gRPC channel's receive window fills. None of these are addressed in the plan.

**Evidence from the plan:** v2:§6.2 L1030, §16.1 decision 3 (L1810–1816), §12.1 L1677–1678. The `tail_app_logs` adapter signature (v2:§6.1 L966–968) returns `AsyncIterator[LogEntry]` with only `since: str | None` as state — no resumption cursor, no dedup handle, no reconnect hook.

**Recommended change:** Add a §6.2a paragraph "Log streaming lifecycle": specify (i) how the adapter detects stream termination, (ii) whether it auto-reconnects, (iii) what `since` marker is threaded through reconnection, (iv) whether the client sees a "stream reset" event, (v) how backpressure propagates from MCP transport to the generator, (vi) the maximum stream duration before the adapter deliberately closes and forces the client to reopen. Also specify the dedup strategy: Modal's internal `TaskLogs` message typically has a monotonic index per task — if the reconnect is by `since=ts`, duplicates on the timestamp boundary are expected; the adapter must either dedup by `(task_id, index)` or accept duplicates and say so.

---

### F5. The `modal.sandboxes.list` and `Sandbox.stdout.read(n)` claims don't match the current sandbox API

**Severity:** Critical

**Why it matters:** `modal.sandboxes.list` being "an async generator richer than CLI" (v2:§6.2 L1038) suggests the author assumes a top-level function `modal.sandboxes.list(environment=...)` that is async-iterable. The actual public Python API for sandbox enumeration in recent Modal versions is closer to `modal.Sandbox.list(app=..., tags=..., environment_name=...)` — it is a classmethod on `Sandbox`, not a module-level function. Second and more load-bearing: `Sandbox.stdout.read(n)` is *not* "give me the last n bytes of stdout". In recent Modal versions `Sandbox.stdout` is a `_StreamReader`, and `.read(n)` without further arguments reads *from the current position forward*, blocking until the sandbox exits or until `n` bytes are available. It is not a tail-read. The `modal_get_sandbox_stdio` tool's `tail_bytes` semantics therefore cannot be implemented with `read(n)`.

**Evidence from the plan:** v2:§6.2 L1038 ("`async for sb in modal.sandboxes.list(…)`"), L1039 (`modal.sandboxes.fromId(…)`), L1040 ("`Sandbox.stdout.read(n)`, `Sandbox.stderr.read(n)`"). v2:§5.1 L559–561 and §5.6 L913. The spec uses `fromId` (camelCase) in L1039, which is the JavaScript SDK naming convention — the Python SDK uses `from_id`. That's a hint that the author cross-read the TS and Python SDKs and may have mixed up which methods exist on which.

**Recommended change:** Verify the current Python sandbox API: (a) the actual classmethod path for listing (`Sandbox.list` vs. `modal.sandboxes.list`), (b) the actual classmethod path for `from_id` (snake_case in Python), (c) the stream reader semantics — whether `read(n)` is forward-read or tail-read, (d) whether there is a dedicated `tail(n)` or a `log_reader` method. If stdio is forward-read, re-specify `modal_get_sandbox_stdio` to either: buffer the entire stream (and cap via `max_bytes`), or require the sandbox to have exited first, or drop the "last N bytes" framing. Also fix the `fromId` → `from_id` typo.

---

### F6. Pagination is assumed cursor-based across every list RPC — but `_grpc_client` RPCs typically return unbounded responses or streaming messages

**Severity:** Critical

**Why it matters:** Every `list_*` method in §6.1 takes a `cursor: Cursor | None` parameter and returns a `Page[...]` with a `next_cursor`. In practice, for `AppList`-style Modal RPCs, the server typically returns all matching entries in a single unary response, or the response is a single-message stream. The CLI's `modal app list` does a full fetch and displays a slice; it does not paginate on the server side. If that's the truth, then the adapter has to either (a) fetch the entire list into memory and paginate client-side (viable for small workspaces, terrible for a workspace with thousands of stopped apps), or (b) accept that `next_cursor` is an adapter-synthesised client-side offset encoded into the opaque `Cursor` payload. Neither is specified.

**Evidence from the plan:** v2:§5.6 L901 ("Cursor + tail"), §5.5 L773–777 (Cursor as opaque), §6.1 L947–998 (every list method takes `cursor: Cursor | None` and returns `Page[...]`).

**Recommended change:** Add a §6.2b paragraph "Pagination reality": for each `list_*` method, state explicitly whether the backing RPC paginates server-side or returns an unbounded response. For the unbounded cases, specify that the adapter fetches the full list, applies search/status filters locally, slices client-side, and encodes the slice offset in the `Cursor` HMAC payload. State the per-call memory cap. Also state whether the adapter caches the full list between paginated calls in the same MCP session.

---

### F7. The "CLI and `_grpc_client` break together" stability argument elides the CLI's own business logic

**Severity:** Major

**Why it matters:** §3.2 argues that because `modal.cli.*` Typer handlers call `modal._grpc_client` directly, the internal client has the same stability profile as the CLI. That's structurally true but misses the point that the CLI adds non-trivial business logic *between* the command and the RPC: pagination loops over unbounded responses, error translation, retry on transient `UNAVAILABLE`/`DEADLINE_EXCEEDED`, path normalisation for volume operations, chunked reads for `VolumeGetFile`, progress bars, and multi-step flows like "rollback = `AppGetHistory` to find target version, then `AppRollback`". The adapter skipping the CLI does not inherit any of this logic — it inherits the raw RPCs and has to reimplement every piece.

**Evidence from the plan:** v2:§3.2 L228–240, §13 L1719–1725, §16.1 decision 6 L1841–1847.

**Recommended change:** Add a §6.2c paragraph "CLI business logic the adapter must replicate": enumerate, per tool, the non-trivial logic the CLI adds on top of the RPC. Start with: (i) `modal_read_volume_text` must reassemble a streaming `VolumeGetFile` response from chunks and stop at `max_bytes` mid-chunk; (ii) `modal_rollback_app` must first call `AppGetHistory` to resolve `target_version=None` into "previous"; (iii) `modal_list_apps` with `status=deployed|running|stopped` has to map onto whatever filter the RPC actually accepts; (iv) `modal_ls_volume` path normalisation must match the CLI's rules; (v) retry on `UNAVAILABLE`. Without this table, the adapter looks like a 10-line gRPC pass-through and is actually a 500-line re-implementation of `modal/cli/app.py`, `modal/cli/volume.py`, and `modal/cli/container.py`.

---

### F8. `rollback_app` semantics are specified with a `target_version: int | None` but Modal's rollback behaviour is not checked against that signature

**Severity:** Major

**Why it matters:** §6.1 L1005–1007 declares `rollback_app(self, app_ref: Ref, target_version: int | None) -> dict`. That implies Modal's `AppRollback` RPC accepts either "previous version" (when `target_version=None`) or a specific version number. Unknown whether (a) `AppRollback` takes a version at all, (b) the version is an `int` or a deployment ID, (c) `None` is a valid "roll back one step" sentinel or whether the caller must pre-resolve it, (d) there is a race condition where two concurrent rollbacks produce non-monotonic deployment versions. If `target_version` must be pre-resolved via `AppGetHistory`, then `modal_rollback_app` is a two-call tool and its idempotency story is different.

**Evidence from the plan:** v2:§6.1 L1005–1007, §5.1 L569–574, §6.2 L1042, §5.1 L563.

**Recommended change:** Verify against the proto: (i) the exact `AppRollback` request message, (ii) how the CLI's `modal app rollback` handles the `--version=` case, (iii) is there a server-side check that the target version is older than the current, (iv) concurrent rollbacks. If `target_version` is required, drop the `| None` default; if it is optional and the server interprets `None` as "previous", note that in §6.2. Add a concrete sentence to the `modal_rollback_app` impact text specifying whether concurrent rollbacks are safe.

---

### F9. `stop_app` irreversibility claim is asserted twice but not verified, and the in-flight-work behaviour is not specified

**Severity:** Major

**Why it matters:** §6.2 L1041 and §13 item 5 L1750–1754 both assert "stopped apps cannot be restarted; recovery is a new deployment". "Cannot be restarted" is a strong claim that ignores: (a) what happens to in-flight function calls at `AppStop` time — cancelled, drained, or allowed to finish? (b) what happens to queued inputs on a `Function.map` that was mid-execution? (c) what happens to containers that are processing at the moment of stop? (d) whether the "stopped" state is reversible via re-deploy of the same name. This is a core load-bearing claim for the dry-run `impact` text and for the threat model §10.2 entry.

**Evidence from the plan:** v2:§6.2 L1041, §10.2 L1492–1498, §13 item 5 L1750–1754.

**Recommended change:** Before `modal_stop_app` ships even as a gated tool, verify and document: (i) exact `AppStop` semantics for in-flight containers — killed, drained, graceful? (ii) behaviour for queued inputs — rejected, retried, lost? (iii) whether re-deploying the same app name restores state or produces a new distinct app; (iv) whether there's a `--graceful` flag on the CLI. Replace the single-sentence impact text in §6.2 L1041 with a three-sentence impact breakdown.

---

### F10. `stop_container` SIGINT + reassignment claim needs verification and does not define "reassigns in-progress inputs"

**Severity:** Major

**Why it matters:** §6.2 L1043 says `ContainerStop` "sends SIGINT to the container and reassigns in-progress inputs to other containers" and the threat model L1494 repeats it. "Reassigns" is load-bearing: does Modal actually guarantee that the input lands on another container, or is it best-effort and the input can be dropped if no peer exists? Does the reassignment preserve the input's dedup key / retry count? What does "in-progress" mean at the moment of SIGINT? Does SIGINT translate to a Python `KeyboardInterrupt` or a gRPC stream cancel?

**Evidence from the plan:** v2:§6.2 L1043, §10.2 L1492–1498.

**Recommended change:** Verify by reading `modal.cli.container.stop` source and the `ContainerStop` RPC's handler doc. Specify in §6.2 L1043: (i) whether reassignment is guaranteed or best-effort; (ii) whether retry count increments on reassignment; (iii) whether the stopped container's outputs (logs, stdout) are preserved post-stop; (iv) whether stopping the last container of a running function causes new containers to spawn or whether the function becomes idle.

---

### F11. The §9.1 internal-API drift probe as described catches only a narrow class of drift

**Severity:** Major

**Why it matters:** §9.1 describes the drift probe as "imports every `modal.*` symbol the adapter references and asserts it exists with the expected signature shape". That catches (i) symbols being deleted, (ii) symbols being renamed, and (iii) function arity changing if `inspect.signature` is checked. It does NOT catch: (a) protobuf field renames inside a gRPC request/response message — the attribute still exists, the signature still matches, the call compiles, and the adapter silently sends a bad request; (b) semantic changes to argument meaning; (c) changes to iteration shape (e.g., `tail_logs` now yields batches instead of single entries); (d) changes to response field types; (e) new required fields on a request message.

**Evidence from the plan:** v2:§9.1 L1338–1348, §8.3 L1313–1315, §10.2 L1483–1491.

**Recommended change:** Re-describe §9.1's drift defences as a two-layer system: (i) **symbol probe** — fast, catches renames and deletions, runs in every CI build; (ii) **fixture replay probe** — slow, drives the adapter against recorded protobuf responses for every RPC, catches field renames, shape changes, and semantic drift. Both must be green for an upgrade. Add a §9.1 note that the symbol probe *alone* is insufficient and enumerate the four failure modes it misses. Also add a fixture capture script (`scripts/capture_modal_fixtures.py`) that runs against a live restricted environment and records gRPC request/response pairs.

---

### F12. `modal.Client` construction, auth refresh, and async lifecycle are not specified

**Severity:** Major

**Why it matters:** The spec says "direct in-process imports from the `modal` package" and "`modal.Client(...).verify()` equivalent" in §6.2 L1021, but never states: (a) how the adapter constructs the `Client`, (b) whether the `Client` is a singleton for the process lifetime or re-created per request, (c) whether the `Client` owns a persistent gRPC channel and what happens if that channel dies, (d) whether token refresh exists for Modal, (e) whether `Client` is safe to share across asyncio tasks, (f) how the test harness constructs a `Client` against the mock adapter.

**Evidence from the plan:** v2:§5.4 L722–729, §6.2 L1021, §6.1 L933–943.

**Recommended change:** Add §6.1a "Modal client lifecycle": (i) exactly how the adapter builds `modal.Client`, (ii) whether the `Client` is process-singleton or per-request, (iii) channel-failure handling, (iv) task-safety, (v) test-harness construction. Until this is specified, the adapter's error handling is underdefined and the hosted mode's token-to-Client bridging is unspecified.

---

### F13. `MODAL_ENVIRONMENT` threading through every RPC is asserted but not mapped to the actual RPC fields

**Severity:** Major

**Why it matters:** §7.2 claims that the server defaults to a single environment and that cross-environment calls are rejected at the adapter boundary with `SCOPE_VIOLATION`. That requires the adapter to *know* which environment every RPC is running against, which in turn requires every outbound RPC to carry an `environment_name` field. In Modal's gRPC surface, not every RPC takes an `environment_name` — some default to the workspace's default environment if none is passed. If the adapter calls an RPC that silently falls back, a caller who passes an `environment_ref` pointing at env A may get results from env B, and the `SCOPE_VIOLATION` check never fires.

**Evidence from the plan:** v2:§2.3 L111, §7.2 L1134–1139, §8.1 L1284, §8.3 L1305.

**Recommended change:** Add to §6.2 a per-row "Environment threading" column. For each RPC, state whether it accepts an explicit `environment_name` field and whether omitting it falls back to the workspace default. For the RPCs where the environment is implicit, document how the adapter verifies the object's env matches the requested `env_ref`. Add a startup assertion that `MODAL_ENVIRONMENT` resolves to a real env id, and fail fast if it does not.

---

### F14. `modal.volumes.Volume.objects.list(...)` and `_VolumeManager.list` paths are inconsistent and likely not real

**Severity:** Critical

**Why it matters:** §6.2 L1034 says `list_volumes` is implemented via "`modal.volumes.Volume.objects.list(…)` (via `_VolumeManager.list`)", and §13 L1706 says "`modal._VolumeManager.list`". The `Volume.objects.list` syntax does not match any Modal Python SDK idiom — `objects` is a Django/ORM convention, not Modal. The plan also declares `ls_volume` as `modal.volume._Volume.iterdir(path=…) equivalent via _grpc_client VolumeListFiles` — mixing two candidate APIs in one cell without picking one.

**Evidence from the plan:** v2:§6.2 L1034–1035, §13 L1706.

**Recommended change:** Pick one path per row and verify it exists. For `list_volumes`, the plausible real path is `modal.Volume.list(environment_name=...)` (classmethod) or an `_grpc_client.VolumeList` RPC. For `ls_volume`, check whether `modal.Volume.iterdir(path)` is public; if yes, use it; if no, drop the reference. Remove the `Volume.objects.list` syntax unless evidence shows it was added.

---

### F15. `AppGetHistory` as "list deployments" ignores that `modal app history` has a specific shape the adapter must reproduce

**Severity:** Minor

**Why it matters:** §6.2 L1028 maps `list_app_deployments` to `AppGetHistory` RPC. The `modal app history` CLI output shows *version*, *commit-ish*, *timestamp*, *status*, *client version*, and sometimes *image layer digest*. The adapter's `Deployment` domain type is not defined in the visible spec text — there is no `Deployment` schema anywhere in §5 or §6. The implementer will need to invent one.

**Evidence from the plan:** v2:§6.2 L1028, §6.1 L959–961, §4 L398.

**Recommended change:** Either add a §6.1b "Domain type schemas" listing the Pydantic model fields for `Workspace`, `Environment`, `App`, `Deployment`, `Container`, `VolumeSummary`, `VolumeEntry`, `SandboxSummary`, `LogEntry`, `LogSummary`, or mark §6.1 as "types defined by `domain/types.py` — to be specified alongside the RPC shape verification in F1". Without this, contract tests in §9.1 can't snapshot anything concrete.

---

### F16. `whoami` via "read profile + call list_environments" hides a subtle identity issue

**Severity:** Minor

**Why it matters:** §6.2 L1022 specifies `whoami` as "Read `modal.config` profile + call `list_environments` to confirm". That gives you the workspace name and confirms the token works, but `modal_whoami` as a discovery tool is probably expected to answer "what principal am I — a human user or a service user, and with what role". Modal distinguishes human users and service users, and the role matters for the safety banner. Reading the profile gives you the token id, not the role.

**Evidence from the plan:** v2:§6.2 L1022, §5.1 L540, §7.2 L1127–1143.

**Recommended change:** Either (i) add a Modal RPC to the `whoami` implementation that returns principal identity and role, or (ii) document that `modal_whoami` returns only token type inferred from the token prefix and that role is not knowable without calling a workspace admin RPC, and surface this limitation in `modal_discovery_server_info`'s safety banner.

---

**Count of items that would likely cost a day or more of v0 rework if caught later:** F1, F2, F3, F5, F6, F7, F9, F12, F14 are the nine strongest. F4, F8, F10, F11, F13 are each good for a half-day or worse depending on what the real Modal behaviour turns out to be. The irreducible truth is that the spec has one giant unverified claim — "these RPCs exist with these names and shapes" — and until it is verified against `modal_proto/api.proto` and the current `modal/cli/*.py` sources, the §6.2 table is a wish list, not an implementation contract.
