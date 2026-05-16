# Annotation-Based Policy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace substring-based tool classification in `classify_tool()` with annotation-driven classification that reads `tool.annotations` and `tool.tags` from the FastMCP tool registry.

**Architecture:** `PolicyMiddleware` currently classifies tools by matching substrings in the tool name against a hardcoded frozenset and `if "log" in tool_name` chains. The new `classify_tool` becomes an async method on `PolicyMiddleware` that calls `await self._mcp.get_tool(name)` (FastMCP's verified async API) to read `Tool.annotations` (`ToolAnnotations | None`) and `Tool.tags` (the toolset name) registered at startup. `PolicyMiddleware.__init__` gains an `mcp: FastMCP[Any]` parameter, and `server.py` passes the `mcp` instance at construction time (toolsets are registered right after, so the middleware lookup is always at call time, not init time — safe).

**Tech Stack:** Python 3.12, FastMCP (`fastmcp.server.server.FastMCP`, `fastmcp.tools.base.Tool`), `mcp.types.ToolAnnotations`, `pytest`, `ruff`

---

## Verified FastMCP API (SPIKE result)

The following was confirmed by reading installed FastMCP source in `.venv/`:

```python
# FastMCP.get_tool is ASYNC — signature verified in server.py line 680:
tool: Tool | None = await mcp.get_tool(name)   # name: str

# Tool.annotations field — verified in tools/base.py line 151-153:
tool.annotations: ToolAnnotations | None  # None when not set at registration

# Tool.tags field — verified in utilities/components.py line 112:
tool.tags: set[str]   # e.g. {"apps"}, {"change"}, {"expert"}, {"containers"}

# ToolAnnotations fields — verified in mcp/types.py line 1262-1268:
tool.annotations.readOnlyHint: bool | None    # True in READ_ONLY_ANNOTATIONS
tool.annotations.destructiveHint: bool | None  # True in MUTATING_ANNOTATIONS
```

Every toolset already registers with the correct annotations:

- `READ_ONLY_ANNOTATIONS = ToolAnnotations(readOnlyHint=True, idempotentHint=True)` — all read-only toolsets
- `MUTATING_ANNOTATIONS = ToolAnnotations(readOnlyHint=False, destructiveHint=True)` — `change` and `expert` toolsets

Tags carry the toolset name exactly as used by `evaluate()`: `"apps"`, `"logs"`, `"containers"`, `"volumes"`, `"sandboxes"`, `"discovery"`, `"change"`, `"expert"`.

**No alternative approach needed.** The proposed API works exactly as described in the ticket.

---

## File Structure

Files changed:

```text
src/modal_mcp/policy/engine.py     # Main change: classify_tool → async method, mcp param
src/modal_mcp/server.py            # Pass mcp= to PolicyMiddleware
tests/unit/test_policy.py          # New classify_tool tests; update middleware fixtures
```

Files read-only (no changes):

```text
src/modal_mcp/toolsets/_common.py  # READ_ONLY_ANNOTATIONS / MUTATING_ANNOTATIONS stay as-is
src/modal_mcp/toolsets/*.py        # All toolset registrations already correct
src/modal_mcp/policy/rules.py      # evaluate() unchanged; toolset string still required
```

---

## Tasks

### Task 1 — Write failing tests for annotation-based `classify_tool`

- [ ] In `tests/unit/test_policy.py`, add a `pytest.fixture` that builds a minimal `FastMCP` instance with three tools:

  ```python
  import pytest
  from typing import Any
  from fastmcp import FastMCP
  from mcp.types import ToolAnnotations
  from modal_mcp.toolsets._common import READ_ONLY_ANNOTATIONS, MUTATING_ANNOTATIONS

  @pytest.fixture
  def annotation_mcp() -> FastMCP[Any]:
      mcp: FastMCP[Any] = FastMCP(name="test-classify")

      @mcp.tool(name="test_read", tags={"apps"}, annotations=READ_ONLY_ANNOTATIONS)
      def test_read() -> str:
          return "ok"

      @mcp.tool(
          name="test_write",
          tags={"change"},
          annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
      )
      def test_write() -> str:
          return "ok"

      @mcp.tool(name="test_dangerous", tags={"expert"}, annotations=MUTATING_ANNOTATIONS)
      def test_dangerous() -> str:
          return "ok"

      @mcp.tool(name="test_no_annotations", tags={"discovery"})
      def test_no_annotations() -> str:
          return "ok"

      return mcp
  ```

- [ ] Add four async tests that import `PolicyMiddleware` and call the new `await middleware.classify_tool(name)`:

  ```python
  @pytest.mark.asyncio
  async def test_classify_tool_read_only_annotation(annotation_mcp: FastMCP[Any]) -> None:
      """Tool with readOnlyHint=True is classified as read-only (non-mutating)."""
      middleware = PolicyMiddleware(annotation_mcp, policy_settings_minimal())
      policy = await middleware.classify_tool("test_read")
      assert policy.mutating is False
      assert policy.toolset == "apps"

  @pytest.mark.asyncio
  async def test_classify_tool_destructive_annotation(annotation_mcp: FastMCP[Any]) -> None:
      """Tool with destructiveHint=True is classified as mutating."""
      middleware = PolicyMiddleware(annotation_mcp, policy_settings_minimal())
      policy = await middleware.classify_tool("test_dangerous")
      assert policy.mutating is True
      assert policy.toolset == "expert"

  @pytest.mark.asyncio
  async def test_classify_tool_write_not_destructive(annotation_mcp: FastMCP[Any]) -> None:
      """Tool with readOnlyHint=False but destructiveHint=False is write but non-mutating."""
      middleware = PolicyMiddleware(annotation_mcp, policy_settings_minimal())
      policy = await middleware.classify_tool("test_write")
      assert policy.mutating is False
      assert policy.toolset == "change"

  @pytest.mark.asyncio
  async def test_classify_tool_unknown_tool_falls_back_to_discovery(
      annotation_mcp: FastMCP[Any],
  ) -> None:
      """Unknown tool name returns non-mutating discovery toolset (safe default)."""
      middleware = PolicyMiddleware(annotation_mcp, policy_settings_minimal())
      policy = await middleware.classify_tool("completely_unknown_tool")
      assert policy.mutating is False
      assert policy.toolset == "discovery"
  ```

  Where `policy_settings_minimal()` reuses the existing `policy_settings` fixture logic (or extracts a helper).

- [ ] Run `uv run pytest tests/unit/test_policy.py -k "test_classify_tool"` and confirm **all four tests fail** with `TypeError` or `AttributeError` (the new API does not exist yet).

---

### Task 2 — Make `classify_tool` an async method on `PolicyMiddleware`; inject `mcp`

- [ ] In `src/modal_mcp/policy/engine.py`:

  1. Add `FastMCP` import at the top of the file:

     ```python
     from typing import Any
     from fastmcp import FastMCP
     ```

     (Note: `Any` is already imported via `from typing import Any` — only add `FastMCP` if not already present.)

  2. Add `mcp` as the **first positional parameter** of `PolicyMiddleware.__init__`:

     ```python
     def __init__(
         self,
         mcp: FastMCP[Any],
         settings: Settings,
         *,
         approval_ledger: ApprovalTokenLedger | None = None,
         rate_limiter: TokenBucketRateLimiter | None = None,
         mutation_limiter: TokenBucketRateLimiter | None = None,
         actor_resolver: ActorResolver | None = None,
         audit_sink: Any | None = None,
         signing_keys: Sequence[tuple[str, bytes]] | None = None,
         now: Callable[[], int] | None = None,
     ) -> None:
         self._mcp = mcp
         self.settings = settings
         # ... rest unchanged ...
     ```

  3. Replace the module-level `classify_tool` function with an async method on the class:

     ```python
     async def classify_tool(self, tool_name: str) -> ToolPolicy:
         """Classify a tool using FastMCP annotation metadata.

         Falls back to MUTATING_TOOLS for tools that are not currently registered
         in the FastMCP registry (e.g. after stubs are deleted but before real
         implementations exist). This preserves approval-flow correctness across
         the delete-stubs → annotation-based-policy implementation sequence.
         """
         tool = await self._mcp.get_tool(tool_name)
         if tool is None:
             # Tool not in registry — fall back to static list (safe default)
             mutating = tool_name in MUTATING_TOOLS
             toolset = "change" if mutating else "discovery"
             return ToolPolicy(tool_name=tool_name, toolset=toolset, mutating=mutating)

         # Derive toolset from the first registered tag; fall back to "discovery"
         toolset = next(iter(sorted(tool.tags)), "discovery")

         annotations = tool.annotations
         if annotations is not None and annotations.destructiveHint is True:
             return ToolPolicy(tool_name=tool_name, toolset=toolset, mutating=True)

         return ToolPolicy(tool_name=tool_name, toolset=toolset, mutating=False)
     ```

  4. Update `on_call_tool` to await the method:

     ```python
     tool_policy = await self.classify_tool(params.name)
     ```

  5. Remove the standalone module-level `classify_tool` function entirely.

  6. Keep `MUTATING_TOOLS` frozenset — it is still consulted as a fallback when
     `get_tool()` returns `None` (see step 3 comment). Do NOT delete it.

  7. Keep `MUTATING_TOOLS` in `__all__`.

- [ ] Run `uv run pytest tests/unit/test_policy.py -k "test_classify_tool"` — confirm all four new tests **pass**.

---

### Task 3 — Update `server.py` to pass `mcp` to `PolicyMiddleware`

The middleware is constructed before `register_toolsets` in `server.py` (lines 395-401), but `classify_tool` is called per-request (at call time), so the middleware only needs to hold a reference to the server — the tools are looked up lazily.

- [ ] In `src/modal_mcp/server.py`, update the `PolicyMiddleware` construction:

  ```python
  mcp.add_middleware(
      PolicyMiddleware(
          mcp,                        # new first arg
          resolved_settings,
          approval_ledger=approval_ledger,
          audit_sink=resolved_audit_sink,
      )
  )
  ```

- [ ] Run `uv run pytest tests/unit/test_policy.py` to confirm **all existing tests still pass**.

---

### Task 4 — Update existing middleware integration tests that construct `PolicyMiddleware`

The existing tests in `test_policy.py` construct `PolicyMiddleware(policy_settings, ...)` — now the signature is `PolicyMiddleware(mcp, settings, ...)`. Each call site must pass a `FastMCP` instance.

- [ ] Search for every `PolicyMiddleware(` call in `tests/unit/test_policy.py`:
  - `test_policy_middleware_consumes_approval_strips_token_and_redacts`
  - `test_policy_middleware_forwards_normalized_dry_run`
  - `test_policy_middleware_consumes_approval_after_signing_env_scrub`
  - `test_policy_middleware_blocks_unapproved_mutation`

- [ ] For each test, add a minimal `FastMCP` fixture with `modal_stop_app` registered
  using `MUTATING_ANNOTATIONS` and tag `"change"`. Do NOT import from
  `modal_mcp.toolsets.change` — that file is deleted by the delete-stubs plan
  which runs before this one:

  ```python
  @pytest.fixture
  def middleware_mcp() -> FastMCP[Any]:
      mcp: FastMCP[Any] = FastMCP(name="test-middleware")

      @mcp.tool(
          name="modal_stop_app",
          tags={"change"},
          annotations=MUTATING_ANNOTATIONS,
      )
      def modal_stop_app(
          app_ref: str,
          dry_run: bool = True,
          approval_token: str | None = None,
      ) -> str:
          return "disabled"

      return mcp
  ```

- [ ] Update each middleware test to use `PolicyMiddleware(middleware_mcp, policy_settings, ...)`.

- [ ] Run `uv run pytest tests/unit/test_policy.py` — confirm **all tests pass**.

---

### Task 5 — Remove the substring fallback from `engine.py`; keep `MUTATING_TOOLS`

By Task 2 the module-level `classify_tool` function is gone. `MUTATING_TOOLS` is intentionally retained as a fallback in the new async method. Confirm cleanup:

- [ ] Verify the old module-level `classify_tool` function no longer exists in `src/`:

  ```bash
  grep -n "^def classify_tool" src/modal_mcp/policy/engine.py
  ```

  Expected: zero matches (it is now a method, not a module-level function).

- [ ] Verify `MUTATING_TOOLS` is still present (retained as fallback):

  ```bash
  grep -n "MUTATING_TOOLS" src/modal_mcp/policy/engine.py
  ```

  Expected: at least two matches (the definition and the fallback lookup in `classify_tool`).

- [ ] Verify no substring patterns remain:

  ```bash
  grep -n '"log" in tool_name\|"container" in tool_name\|"volume" in tool_name\|"sandbox" in tool_name\|"app" in tool_name\|"deployment" in tool_name' src/modal_mcp/policy/engine.py
  ```

  Expected: zero matches.

- [ ] Run full suite: `uv run pytest` — all tests pass.

---

### Task 6 — Linting and full verification

- [ ] `uv run ruff check .` — zero issues.
- [ ] `uv run ruff check . --fix` if there are auto-fixable issues, then re-check.
- [ ] `uv run pytest` — full suite green.
- [ ] Review `__all__` in `engine.py`: `MUTATING_TOOLS` remains exported (it is still consulted as a fallback when `get_tool()` returns `None` — see Task 4 step 6). `classify_tool` is NOT exported (it is now a method on `PolicyMiddleware`, not a public function).

---

## Edge Cases

| Scenario | Behavior |
|---|---|
| Tool not found (`get_tool` returns `None`) | Returns `ToolPolicy(toolset="discovery", mutating=False)` — safest default, blocks if `discovery` disabled |
| Tool has `annotations=None` (no annotations) | `mutating=False`; toolset from `tool.tags` or `"discovery"` |
| Tool has multiple tags | `next(iter(sorted(tool.tags)))` — lexicographically first; all modal tools have exactly one toolset tag |
| `annotations.destructiveHint` is `None` | Falls through to `mutating=False` (non-destructive default) |
| `annotations.destructiveHint` is `False` | `mutating=False` (e.g., `test_write` fixture above) |

---

## Self-Review

**Correctness:** All existing toolsets already declare the correct annotations — `change` and `expert` use `MUTATING_ANNOTATIONS` (`destructiveHint=True`), all read-only toolsets use `READ_ONLY_ANNOTATIONS`. No toolset registration changes are needed.

**Async boundary:** `classify_tool` is now `async` (calls `await self._mcp.get_tool()`). `on_call_tool` is already `async`, so the `await` composes cleanly. The module-level `classify_tool(tool_name: str) -> ToolPolicy` function in the public `__all__` is removed — callers that depended on it (only `server.py`'s middleware wiring, which is internal) are updated in Task 3.

**Ordering:** `PolicyMiddleware` is added to `mcp` before `register_toolsets` is called. This is safe because `classify_tool` is invoked at request-handling time, not at registration time. By the time any request arrives, all tools are registered.

**No tag collision risk:** Modal tool tags are single-element sets by design. The `sorted(...)[0]` pattern is deterministic even for multi-tag tools.

**What this does NOT change:** `evaluate()` in `rules.py` is unchanged — it still receives a `toolset` string and applies the read-only/enabled-toolsets gate. The annotation change only affects how `ToolPolicy.toolset` and `ToolPolicy.mutating` are determined before `evaluate()` is called.
