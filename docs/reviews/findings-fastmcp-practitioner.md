# FastMCP practitioner review — `modal-mcp_v2.md`

**Bottom line.** The plan has a solid architectural story, but its FastMCP wiring was written from the v2.x / pre-merge mental model and never re-checked against what 3.2.x actually exposes. Four of the five load-bearing FastMCP APIs the document names — `ctx.get_state` without `await`, `mcp.set_default_state`, the `@tool_call_middleware` decorator with a `(ctx, tool_name, arguments, call_next)` signature, and a Starlette mount that drops FastMCP's lifespan — are wrong at the code level and will not work as written. On top of that, the `ToolEnvelope[T]` auto-schema story leans on a Generic TypeVar resolution that FastMCP does not perform, and the lifespan sketch tries to bootstrap a per-process adapter through a per-session state store. The plan's framework claims in §3.3 were "verified against docs", but the decorator sketches in §5.2, §5.4, and §7.6 contradict those docs. A v0 prototype hits three of these in the first day and can't land a single tool call end-to-end until the adapter-injection model and the ASGI composition are rewritten.

---

## F1. `ctx.get_state` is awaited in v3 — the `modal_list_apps` sketch will blow up with `AttributeError`/coroutine-never-awaited

**Severity:** Critical

**Why it matters:** In FastMCP 3.x, `ctx.set_state` and `ctx.get_state` were converted to async methods (this was the headline change in the v3 session-state rewrite). The spec's tool body does `adapter: ModalAdapter = ctx.get_state("modal_adapter")` — a synchronous assignment — so `adapter` is a coroutine, not a `ModalAdapter`. The very first method call (`await adapter.list_apps(...)`) raises `AttributeError: 'coroutine' object has no attribute 'list_apps'` and emits an unawaited-coroutine warning. Every tool in the `toolsets/` tree that copies this template is broken. Implementer hits this on the first `tools/call` in the v0 prototype.

**Evidence from the plan:** v2:§5.2, L660 — `adapter: ModalAdapter = ctx.get_state("modal_adapter")`. The FastMCP docs explicitly say: "Methods like `ctx.get_state()` and `ctx.set_state()` are now async" and every example is `count = await ctx.get_state("counter")`.

**Recommended change:** Every `get_state` call must be `await`ed: `adapter: ModalAdapter = await ctx.get_state("modal_adapter")`. Same fix in every toolset module. Add a mypy-strict rule (or a grep-level CI check) that flags unawaited `ctx.get_state(` and `ctx.set_state(` in `src/modal_mcp/toolsets/`.

---

### F2. `mcp.set_default_state(...)` does not exist in FastMCP 3.2.x — the lifespan-based adapter injection is a phantom API

**Severity:** Blocker

**Why it matters:** The plan's entire request-scoped dependency injection story rests on one line: `await mcp.set_default_state("modal_adapter", adapter)` inside the lifespan. There is no such method on `FastMCP` in 3.2.x. The only state APIs are `ctx.set_state` / `ctx.get_state` / `ctx.delete_state`, and they are **session-scoped** — FastMCP's own docs are explicit: "Store data that persists across multiple requests within the same MCP session. Session state is automatically keyed by the client's session, ensuring isolation between different clients." State written inside a Starlette lifespan hook is not attached to any session at all, so even if a `set_default_state` did exist, you could not seed session state from a lifespan — there's no session yet. The adapter is a process-wide singleton; the mechanism the plan picked is per-client. An implementer discovers this on day one of v0 and has to redesign how every tool gets its adapter.

**Evidence from the plan:** v2:§5.4, L721–L729:
```python
@asynccontextmanager
async def lifespan(app):
    adapter = await ModalAdapter.create(settings)
    try:
        await mcp.set_default_state("modal_adapter", adapter)
        yield
    ...
```
Plus v2:§6.1 which expects `ctx.get_state("modal_adapter")` to always return the singleton. No `set_default_state` in FastMCP's source, changelog, or docs in 3.x.

**Recommended change:** Drop session state as the injection mechanism entirely. Use one of two patterns that FastMCP actually supports:
1. **Module-global adapter** constructed in the FastMCP lifespan and imported directly by each toolset module — the `ModalAdapter` is a process-wide singleton, and FastMCP's lifespan hook (passed as `FastMCP(lifespan=...)`) is the right place to build it. Tools close over the module global; there is no DI through `ctx`.
2. **`fastmcp.dependencies` / `CurrentContext` + a small app-state accessor** where the adapter lives on a module-level holder. FastMCP's own `Context` examples use `Context = CurrentContext()` as the default, not `Context = ...`. The `ctx` handle is for per-request metadata, not dependency injection.

Either way, the §5.2 sketch needs to stop reading the adapter from `ctx.get_state` and read it from a module global.

---

### F3. `@tool_call_middleware` with `(ctx, tool_name, arguments, call_next)` is not how FastMCP middleware is written — the policy engine won't load

**Severity:** Critical

**Why it matters:** FastMCP 3.x exposes middleware as a **class-based** API: you subclass `fastmcp.server.middleware.Middleware` and override `on_call_tool`, `on_message`, etc., with a signature of `(self, context: MiddlewareContext, call_next)`. There is no `@tool_call_middleware` decorator in `fastmcp.server.middleware` — it does not exist. The plan's `policy_middleware` function will fail to import. Even if a decorator by that name existed, the `(ctx, tool_name, arguments, call_next)` shape doesn't match any FastMCP middleware hook: `tool_name` and `arguments` live on `context.message` / `context.message.params`, not as positional parameters. The plan also writes `mcp.add_middleware` nowhere — the §7.6 sketch is a floating decorator that is never registered with the server. Implementer discovers this the moment they wire in the policy engine (~day 2 of v0).

**Evidence from the plan:** v2:§7.6, L1229–L1248:
```python
from fastmcp.server.middleware import tool_call_middleware

@tool_call_middleware
async def policy_middleware(ctx, tool_name, arguments, call_next):
    ...
```
Compare to FastMCP docs: middleware is `class MyMiddleware(Middleware): async def on_call_tool(self, context: MiddlewareContext[mt.CallToolRequestParams], call_next: CallNext[...]) -> ToolResult`, registered with `mcp.add_middleware(MyMiddleware())`.

**Recommended change:** Rewrite the policy engine as a `Middleware` subclass:
```python
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.tools.tool import ToolResult
import mcp.types as mt

class PolicyMiddleware(Middleware):
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        params = context.message  # CallToolRequestParams
        tool_name = params.name
        arguments = dict(params.arguments or {})
        # ... policy decision ...
        arguments.pop("approval_token", None)
        params.arguments = arguments
        return await call_next(context)
```
Register with `mcp.add_middleware(PolicyMiddleware())` in `server.py`. Related gotcha for argument stripping: Pydantic input-model validation happens *after* `on_call_tool`, so stripping works — but if `approval_token` is also declared as a field on the tool's Pydantic input model, Pydantic will reject it when the stripped arguments re-validate. Either declare approval_token on the input model AND strip in middleware, or use an out-of-band carrier.

---

### F4. Mounting `mcp.streamable_http_app()` under a parent Starlette app without propagating the lifespan silently breaks Streamable HTTP session management

**Severity:** Critical

**Why it matters:** FastMCP's HTTP app owns the **Streamable HTTP session manager**, and that manager is started inside the ASGI app's **lifespan**. The FastMCP docs are explicit: *"it is critical to pass the lifespan context from the FastMCP app to the main application. Failure to do so prevents the proper initialization of the session manager, which is required for the Streamable HTTP transport to function correctly."* The §3.4 sketch constructs a `Starlette(routes=..., middleware=...)` with no `lifespan=` argument, then calls `app.mount("/", mcp.streamable_http_app())` — the child's lifespan is dropped on the floor, the session manager never starts, and every `tools/call` after `initialize` returns a 500 or silently misroutes. The sketch also uses the older `streamable_http_app()` name; 3.x canonical is `mcp.http_app(path="/mcp")`.

On top of that, the plan uses `Mount("/" , ...)` at the root AND expects a `/mcp` endpoint — but `http_app` / `streamable_http_app` already mount at `/mcp` internally, so mounting at `/` is just duplicative. The plan also passes middleware via Starlette's `middleware=[...]` kwarg, but FastMCP recommends `mcp.http_app(middleware=[...])` so that middleware wraps the FastMCP ASGI app *including* its lifespan.

**Evidence from the plan:** v2:§3.4, L276–L293:
```python
app = Starlette(
    routes=[...],
    middleware=[...],
)
app.mount("/", mcp.streamable_http_app())
```
No `lifespan=` argument; no `@asynccontextmanager` chain back to the mcp child's lifespan.

**Recommended change:** Replace with the documented pattern:
```python
from starlette.applications import Starlette
from starlette.routing import Mount

mcp_app = mcp.http_app(
    path="/mcp",
    middleware=[
        Middleware(OriginGuard, allowed_origins=settings.allowed_origins),
        Middleware(RateLimitMiddleware, settings=settings.rate_limit),
    ],
)

app = Starlette(
    routes=[Mount("/", app=mcp_app)],
    lifespan=mcp_app.lifespan,  # MANDATORY
)
```
Then put the §5.4 `ModalAdapter.create(...)` lifecycle **inside** the FastMCP lifespan passed to `FastMCP(lifespan=...)`, not on the Starlette wrapper. Decide which layer owns which middleware.

---

### F5. `ToolEnvelope[T]` does not auto-generate a concrete `output_schema` — FastMCP sees the unresolved TypeVar

**Severity:** Critical

**Why it matters:** The plan (§3.3, §5.2, §5.3, §5.5) repeatedly claims that declaring a tool's return annotation as `ToolEnvelope[AppListData]` will cause FastMCP to "auto-generate the wrapped `output_schema`". FastMCP's structured-output machinery builds a schema from `pydantic.TypeAdapter(<return_annotation>).json_schema()`. That works for concrete Pydantic generics, but the plan's canonical descriptor in §5.5 uses `allOf: [ {$ref: ToolEnvelope}, { properties: { data: {...}}}]` — which implies FastMCP somehow emits a `$ref`-based schema against a shared `$defs/ToolEnvelope`. It does not. Pydantic emits a fully inlined JSON Schema for the concrete generic class (named something like `ToolEnvelope[AppListData]` in `$defs` with mangled characters, typically replacing `[` `]` with `_`), which will neither match the snapshot in §5.5 nor reuse a shared `$defs/ToolEnvelope` definition across tools. The §9.1 contract snapshot test will fail on day 1 of every schema regeneration.

**Evidence from the plan:** v2:§5.3, L699–L702 ("FastMCP generates the wrapped `output_schema` automatically. No hand-written JSON Schema for the wrapper"); v2:§5.5, L829–L864 (the `allOf: [ $ref: ToolEnvelope, ...]` shape).

**Recommended change:** Accept that FastMCP will emit per-tool inlined schemas derived from the concrete `ToolEnvelope[...]` specialisation, and rewrite §5.5's canonical descriptor to match what Pydantic actually emits. Either:
1. Regenerate `schema/mcp-tools.v1.json` from the live `mcp.list_tools()` and commit that as the snapshot (drop the hand-written `allOf`/`$defs` form entirely);
2. **or** pass `output_schema=` explicitly on each `@mcp.tool(...)` to force the `allOf`/`$defs` composition the plan wants, and give up auto-generation. The plan flips between both styles; pick one.

If the goal is a stable, `$ref`-based bundle shared across tools, option 2 is the only reliable path.

---

### F6. Session state is per-session, so even the corrected `ctx.get_state` pattern won't share one adapter across clients

**Severity:** Major

**Why it matters:** Suppose F1/F2 are both fixed. FastMCP state is **session-scoped and per-client-isolated** by design — if you seed it in one tool call, only subsequent calls on the *same* `Mcp-Session-Id` see it. A new client (or a client that reconnects after session expiry) gets an empty state store. There is no process-wide "default state" hook. The plan's architecture wants the adapter to be a process singleton, and the §5.4 lifespan was trying to express that. Under load with many clients coming and going (hosted mode), this will either silently re-run adapter bootstrap per session or raise `None` on every first call. Implementer doesn't hit this in v0 with one client; hits it in v2 (hosted) as soon as multiple clients connect.

**Evidence from the plan:** v2:§3.3 L259 ("Session state across requests ✅ `ctx.set_state / get_state`"); v2:§5.4 lifespan trying to seed that state process-wide. FastMCP's docs explicitly: "Store data that persists across multiple requests within the same MCP session…isolated per client session…expires after 1 day".

**Recommended change:** Stop using `ctx` state for process-wide dependencies. The `ModalAdapter` should live as a module global or on a dedicated accessor imported by each toolset module, constructed inside the FastMCP lifespan. Reserve `ctx.set_state` / `get_state` for genuinely per-session data (e.g., a cached `whoami` result, a per-session rate-limit counter). Update §3.3 row 13 to differentiate "session state for per-session things" from "process-wide adapter sharing (use module global)".

---

### F7. `@mcp.tool(timeout=30.0)` will kill long-running streaming tools before `tail_logs` can emit anything useful

**Severity:** Major

**Why it matters:** The plan sets `timeout=30.0` on `modal_list_apps` (§5.2) and describes the same decorator style as canonical. Separately, the plan promises in §16 and §12.1 that log streaming (`tail_app_logs`, `get_sandbox_stdio`) ships in v1, implemented via `async for batch in tail_logs(...)` + `ctx.report_progress`. FastMCP's `timeout=` kwarg is a hard execution timeout that cancels the tool coroutine — it does not reset on progress notifications. A tail-logs session that runs for 10 minutes with a 30-second timeout is killed at 30 seconds.

Additionally, `ctx.report_progress` only works if `initialize` negotiated the SSE stream. A client that uses the plain POST form of Streamable HTTP with `Accept: application/json` only (not `text/event-stream`) will not see progress notifications at all — they are dropped.

**Evidence from the plan:** v2:§5.2 L646 (`timeout=30.0`); v2:§3.3 L258; v2:§12.1 L1677.

**Recommended change:** Remove `timeout=` from any tool that can stream — `modal_get_app_logs` (if it ever streams), `tail_app_logs` — and either omit it or set it to the configured maximum streaming duration. Document in §5.2 that `timeout=30.0` applies only to non-streaming tools. Also document in §3.4 / §12.1 that the streaming test must open the session with `Accept: application/json, text/event-stream`.

---

### F8. §5.2 tool signature uses `ctx: Context = ...` — this is neither idiomatic nor the documented default

**Severity:** Minor

**Why it matters:** FastMCP 3.x expects `ctx` to be either a positional parameter with a `Context` type annotation or to use `Context = CurrentContext()` as its default. The `= ...` sentinel is not the documented default. In 3.2.x with type-based detection it probably still works, but the signature is non-idiomatic, doesn't match any example in the FastMCP docs, and may drift under a minor release that tightens parameter validation.

**Evidence from the plan:** v2:§5.2 L658 — `ctx: Context = ...`.

**Recommended change:** Use `ctx: Context` with no default. Every FastMCP example does it that way. If the plan wants to be explicit, import `from fastmcp.dependencies import CurrentContext` and use `ctx: Context = CurrentContext()`.

---

### F9. `MultiAuth` with `RemoteAuthProvider`: the plan's claim at §3.3 L257 is ambiguous and mis-specifies composition

**Severity:** Major

**Why it matters:** The §3.3 table says "OAuth (v2) ✅ `RemoteAuthProvider`, `OAuthProxy`, `MultiAuth`". In FastMCP 3.2.x, `MultiAuth` wraps an optional auth *server* (`OAuthProxy` or `RemoteAuthProvider`) plus one or more *verifiers* (`JWTVerifier`). It does **not** compose `RemoteAuthProvider` + `JWTVerifier` side-by-side as equals. There is also a quiet default gotcha: `RemoteAuthProvider.allowed_client_redirect_uris` **defaults to all URIs** (for DCR compatibility). The plan never names that parameter, and §7 has nothing about DCR posture.

**Evidence from the plan:** v2:§3.3 L256–L257 and v2:§7.1 (modes table with `hosted_read_only_ephemeral` and `hosted_mutating_approval`), neither of which discusses DCR or `allowed_client_redirect_uris`.

**Recommended change:** Rewrite §7.1 / §7.6 to name the concrete composition for each mode explicitly. Example for hosted:
```python
auth = MultiAuth(
    server=RemoteAuthProvider(
        token_verifier=JWTVerifier(jwks_uri=..., issuer=..., audience=...),
        authorization_servers=[AnyHttpUrl(settings.auth_issuer)],
        base_url=settings.public_base_url,
        allowed_client_redirect_uris=settings.allowed_redirect_uris,  # MUST be set
    ),
    verifiers=[
        JWTVerifier(jwks_uri=..., issuer=..., audience=...),  # machine-to-machine
    ],
)
```
Document `MODAL_MCP_ALLOWED_REDIRECT_URIS` in §11 and make it required when auth mode is hosted. Replace "bearer token" for self-hosted with a concrete `StaticTokenVerifier` (3.x) or a `JWTVerifier` against a local issuer.

---

### F10. Schema-snapshot determinism is under-claimed — FastMCP/Pydantic minor bumps will churn the snapshot

**Severity:** Major

**Why it matters:** §9.1 specifies a contract test that diffs FastMCP's generated output against a committed `schema/mcp-tools.v1.json`. In practice, the generated schema is sensitive to: (a) Pydantic v2 minor versions (`$defs` naming, `additionalProperties` defaults, `title` casing change between 2.7 → 2.8 → 2.9 for generic class specialisations), (b) FastMCP changes to how it stitches `ToolAnnotations` in, (c) Python version changes to dict iteration, (d) the `ToolEnvelope[T]` generic produces different class names depending on Pydantic's generic-alias resolver. Pinning `fastmcp>=3.2,<4` and `pydantic>=2.7` (no upper bound) gives a huge matrix of valid dep resolutions.

**Evidence from the plan:** v2:§4 L491–L504 (`fastmcp>=3.2,<4`, `pydantic>=2.7` unbounded); v2:§9.1 L1332.

**Recommended change:** Tighten version pins to `fastmcp>=3.2,<3.3` and `pydantic>=2.7,<2.10` for the snapshot-baseline release. Add a **normalisation layer** in `scripts/generate_schemas.py` that sorts object keys, flattens `title` capitalisation, canonicalises the `ToolEnvelope[...]` `$defs` key, and strips Pydantic-injected `title` fields before diffing. Drop the claim in §13 item 2 that the `fastmcp>=3.2,<4` pin is sufficient.

---

### F11. `mcp.disable(tags={...})` called *before* `list_changed` can fire — startup won't emit the notification

**Severity:** Minor

**Why it matters:** §5.4 calls `mcp.disable(tags=disabled)` at module-import time, before any client has connected. FastMCP only emits `notifications/tools/list_changed` when called inside an active MCP request context. Startup-time disable silently suppresses the notification (correct), but §3.3 and §5.2/§5.4 consistently describe "toggling emits list_changed automatically" as if it applies to both startup and runtime toggles. If a v2 operator expects to reload toolsets at runtime from a signal handler outside a request, no notification fires.

**Evidence from the plan:** v2:§5.4 L744–L750; v2:§5.1 L601–L602.

**Recommended change:** Clarify in §5.4 that startup gating is "no-op notification" (correct). If runtime toggling is in scope, add an explicit note that runtime disable/enable must be called from inside a tool or middleware hook, and add an integration test in §9.1 that exercises this path. Consider exposing runtime toggling as a `modal_admin_set_toolsets` tool (enabled only in self-hosted mode).

---

### F12. OTel trace context propagation through FastMCP middleware is asserted, not verified

**Severity:** Minor

**Why it matters:** §8.3 L1318–L1320 says *"FastMCP itself is OTel-compatible via context propagation"* and names concrete attributes (`mcp.method.name`, `mcp.session.id`, `mcp.protocol.version`) as if they are emitted by FastMCP. In 3.2.x, FastMCP does **not** natively emit MCP-spec OTel spans or attributes; there is no FastMCP semantic-convention helper. `opentelemetry-instrumentation-starlette` will wrap the outer Starlette app and produce HTTP-level spans, but the context does not automatically propagate into FastMCP's middleware chain.

**Evidence from the plan:** v2:§8.3 L1302–L1320.

**Recommended change:** Rewrite §8.3 to be explicit: FastMCP does **not** emit OTel spans natively. Add an `OtelMiddleware(Middleware)` subclass in `observability/tracing.py` that reads the current OTel context, starts a manual span with `mcp.method.name=<tool|list|initialize>`, `mcp.session.id=<ctx.session_id>`, `mcp.protocol.version="2025-06-18"`, wraps `await call_next(context)` inside the span, and attaches the span as the parent for adapter calls. The adapter's `modal.sdk.<op>` spans should explicitly pass the current OTel context. Drop the "FastMCP itself is OTel-compatible" claim.

---

### F13. Per-tool `timeout=` plus FastMCP-level input validation plus policy middleware ordering is unspecified

**Severity:** Major

**Why it matters:** §7.3 describes the policy engine's enforcement order (rate limit → toolset gate → read-only → approval → input validation → redaction), but it doesn't say **where in the FastMCP pipeline** this middleware runs relative to (a) FastMCP's own Pydantic input validation against the tool's declared parameters, (b) the per-tool `timeout` countdown, and (c) structured-output schema enforcement. In 3.2.x, the `on_call_tool` middleware hook runs **after** the MCP-layer request is parsed but **before** FastMCP validates the arguments against the tool's Pydantic model. The plan's claim in §7.3 step 5 ("Input validation … FastMCP validates at the MCP SDK layer; we validate again here to defend against any bypass path") is backwards: FastMCP validates *after* middleware. That means the policy engine's step 5 duplicate validation is reading the *unvalidated* arguments, not the post-Pydantic ones.

**Evidence from the plan:** v2:§7.3 L1146–L1176; v2:§7.6 L1229; §3.3.

**Recommended change:** Rewrite §7.3 to name the actual FastMCP pipeline order: `add_middleware` hooks (outer → inner) → tool dispatch → Pydantic `TypeAdapter` validation of `arguments` against the tool's function signature → tool body → Pydantic validation of the return value → inner middleware post-processing. Drop the "FastMCP validates at the MCP SDK layer" claim. Either (a) move policy checks that require validated arguments **inside** the tool body, or (b) run Pydantic validation manually inside the middleware before making the decision, using `pydantic.TypeAdapter(ToolInputModel).validate_python(arguments)`. Add an integration test that exercises policy-deny-with-missing-required-field vs. policy-deny-with-wrong-type.
