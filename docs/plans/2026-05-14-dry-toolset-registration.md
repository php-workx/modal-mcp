# DRY Toolset Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a `register_read_toolset` factory into `toolsets/_common.py` so that the repetitive list/get registration pattern across apps, containers, volumes, sandboxes, and discovery is encoded once, and each entity file becomes a concise registration call.

**Architecture:** The factory lives in `toolsets/_common.py` alongside existing helpers (`page_envelope_partial`, `not_found`). It uses `FastMCP.tool()` internally with the standard `name`, `tags`, and `annotations` kwargs and generates two tools per entity: `modal_list_{entity_plural}` and `modal_get_{entity}`. Entity files that have additional tools beyond list/get (container logs, volume read_file, sandbox exec) keep their custom registrations; only the list/get pair is extracted. A comment block in each migrated file and in `_common.py` documents the scope boundary.

**Tech Stack:** Python 3.12+, FastMCP (`mcp.tool()` decorator with `name`/`tags`/`annotations` params), `uv` for running tests and lint (`uv run pytest`, `uv run ruff check .`).

---

## File Structure

Files touched by this plan:

```text
src/modal_mcp/toolsets/
    _common.py           ← factory + scope-boundary docstring added here
    apps.py              ← list/get replaced with factory call; list_app_deployments kept custom
    containers.py        ← list/get replaced with factory call; modal_get_container_logs kept custom
    volumes.py           ← list_volumes/stat_volume_path replaced; ls_volume and read_volume_text kept custom
    sandboxes.py         ← list/get replaced with factory call; modal_get_sandbox_stdio kept custom
    discovery.py         ← list_environments/get_environment replaced; other discovery tools kept custom
tests/integration/
    test_http_mcp.py     ← unchanged (all existing assertions must continue to pass)
tests/unit/toolsets/
    test_register_read_toolset.py   ← new unit test file for the factory
```

---

## Tasks

### Phase 1 — Understand and write a failing unit test for the factory

- [ ] Create the test file `tests/unit/test_register_read_toolset.py` (flat in `tests/unit/`, matching existing convention — no subdirectory needed) with a test that imports `register_read_toolset` from `modal_mcp.toolsets._common`, constructs a bare `FastMCP()` instance, calls the factory with a minimal entity, and asserts both generated tools are registered with the correct names, tags, and `readOnlyHint=True` annotation.

```python
# tests/unit/test_register_read_toolset.py
"""Unit tests for register_read_toolset factory."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP
from pydantic import BaseModel

from modal_mcp.toolsets._common import register_read_toolset


class Widget(BaseModel):
    id: str
    name: str


def _list_widgets(
    environment_name: str | None = None,
) -> tuple[list[Widget], list[str]]:
    return [Widget(id="w1", name="foo")], []


def _get_widget(widget_ref: str) -> Widget | None:
    return Widget(id="w1", name="foo") if widget_ref == "w1" else None


@pytest.mark.asyncio
async def test_register_read_toolset_creates_two_tools() -> None:
    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_widgets,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "modal_list_widgets" in names
    assert "modal_get_widget" in names


@pytest.mark.asyncio
async def test_list_tool_has_read_only_annotations() -> None:
    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_widgets,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    tools = await mcp.list_tools()
    tool_map = {t.name: t for t in tools}
    assert tool_map["modal_list_widgets"].annotations.readOnlyHint is True
    assert tool_map["modal_list_widgets"].annotations.idempotentHint is True
    assert tool_map["modal_get_widget"].annotations.readOnlyHint is True
    assert tool_map["modal_get_widget"].annotations.idempotentHint is True


@pytest.mark.asyncio
async def test_list_tool_has_correct_tags() -> None:
    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_widgets,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    tools = await mcp.list_tools()
    tool_map = {t.name: t for t in tools}
    assert tool_map["modal_list_widgets"].tags == {"widgets"}
    assert tool_map["modal_get_widget"].tags == {"widgets"}


@pytest.mark.asyncio
async def test_list_tool_returns_page_envelope_partial() -> None:
    """list_fn result is wrapped via page_envelope_partial (warnings preserved)."""
    from fastmcp.tools.base import ToolResult

    warned_items: list[Widget] = [Widget(id="w2", name="bar")]
    warned: list[str] = ["normalization warning"]

    def _list_with_warnings(
        environment_name: str | None = None,
    ) -> tuple[list[Widget], list[str]]:
        return warned_items, warned

    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_with_warnings,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    result: ToolResult = await mcp.call_tool("modal_list_widgets", {})
    # structured content contains items and warnings
    content = result.structured_content
    assert content is not None
    assert content["data"]["items"][0]["id"] == "w2"
    assert "normalization warning" in content["warnings"]


@pytest.mark.asyncio
async def test_get_tool_returns_not_found_on_miss() -> None:
    """get_fn returning None is converted to not_found envelope."""
    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_widgets,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    result = await mcp.call_tool("modal_get_widget", {"widget_ref": "missing"})
    content = result.structured_content
    assert content is not None
    assert content["error"]["code"] == "NOT_FOUND"
    assert "widget not found: missing" in content["error"]["message"]


@pytest.mark.asyncio
async def test_list_tool_passes_environment_name() -> None:
    """environment_name kwarg is forwarded to list_fn."""
    received_env: list[str | None] = []

    def _list_capture(environment_name: str | None = None) -> tuple[list[Widget], list[str]]:
        received_env.append(environment_name)
        return [], []

    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_capture,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    await mcp.call_tool("modal_list_widgets", {"environment_name": "staging"})
    assert received_env == ["staging"]


@pytest.mark.asyncio
async def test_list_tool_extra_params_forwarded() -> None:
    """Extra params declared via extra_list_params are forwarded to list_fn."""
    received_app_ref: list[str | None] = []

    def _list_with_app_ref(
        environment_name: str | None = None,
        app_ref: str | None = None,
    ) -> tuple[list[Widget], list[str]]:
        received_app_ref.append(app_ref)
        return [], []

    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_with_app_ref,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
        extra_list_params=["app_ref"],
    )
    await mcp.call_tool("modal_list_widgets", {"app_ref": "mref1.app"})
    assert received_app_ref == ["mref1.app"]
```

- [ ] Verify the tests fail (ImportError or AttributeError) before the factory exists:

```bash
uv run pytest tests/unit/test_register_read_toolset.py -x 2>&1 | head -30
```

Expected output: `ImportError: cannot import name 'register_read_toolset'`.

---

### Phase 2 — Implement the factory in `_common.py`

- [ ] Edit `src/modal_mcp/toolsets/_common.py` to add the `register_read_toolset` factory. Replace the file content entirely with the following (existing helpers are preserved verbatim):

```python
"""Shared helpers for FastMCP toolsets."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from modal_mcp.domain.envelope import ToolEnvelope, error_result, ok
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.domain.models import Page

READ_ONLY_ANNOTATIONS = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
MUTATING_ANNOTATIONS = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
REQUEST_ID = "tool-call"

T = TypeVar("T", bound=BaseModel)


def envelope[M: BaseModel](data: M) -> ToolEnvelope[M]:
    """Wrap a concrete model in the standard success envelope."""

    return ok(data, request_id=REQUEST_ID)


def page_envelope[M: BaseModel](items: Sequence[M]) -> ToolEnvelope[Page[M]]:
    """Wrap a sequence in the standard paged success envelope."""

    return ok(
        Page[M](items=list(items), truncated=False),
        request_id=REQUEST_ID,
    )


def page_envelope_partial[M: BaseModel](
    items: Sequence[M],
    warnings: Sequence[str],
) -> ToolEnvelope[Page[M]]:
    """Wrap a partial sequence with normalization warnings in a success envelope."""

    return ok(
        Page[M](items=list(items), truncated=False),
        request_id=REQUEST_ID,
        warnings=list(warnings),
    )


def not_found(message: str) -> ToolEnvelope[Any]:
    """Return a normalized not-found tool error."""

    return error_result(
        ModalAdapterError(ErrorCode.NOT_FOUND, message),
        request_id=REQUEST_ID,
    )


def disabled_error(tool_name: str, details: dict[str, Any]) -> ToolEnvelope[Any]:
    """Return a normalized disabled-capability error."""

    return error_result(
        ModalAdapterError(
            ErrorCode.POLICY_BLOCKED,
            f"{tool_name} is disabled in Modal MCP v1",
            details=details,
        ),
        request_id=REQUEST_ID,
    )


# ---------------------------------------------------------------------------
# register_read_toolset — standard list/get factory
#
# Scope: encodes the list/get pattern only (modal_list_{entity} +
# modal_get_{entity}).  Tools with unique parameter shapes or empty-result
# hints are NOT covered:
#   • modal_get_container_logs  — unique time-range params + empty-log hint
#   • modal_ls_volume / modal_read_volume_text / modal_stat_volume_path
#     — volume-path params not part of the list/get pattern
#   • modal_get_sandbox_stdio — bounded-buffer params
# These keep custom registration in their respective module files.
# ---------------------------------------------------------------------------


def _pluralise(entity_name: str) -> str:
    """Simple English pluralisation sufficient for current entity names."""
    if entity_name.endswith("x"):
        return entity_name + "es"
    if entity_name.endswith("y") and not entity_name[-2] in "aeiou":
        return entity_name[:-1] + "ies"
    return entity_name + "s"


def register_read_toolset(
    mcp: FastMCP[Any],
    entity_name: str,
    list_fn: Callable[..., tuple[Sequence[T], list[str]]],
    get_fn: Callable[[str], T | None],
    get_param_name: str,
    not_found_message_template: str,
    tags: set[str],
    extra_list_params: list[str] | None = None,
) -> None:
    """Register a standard list/get tool pair for one entity type.

    Parameters
    ----------
    mcp:
        The FastMCP instance to register tools on.
    entity_name:
        Singular snake_case entity name, e.g. ``"app"``, ``"container"``.
        Tool names are derived as ``modal_list_{plural}`` and
        ``modal_get_{entity_name}``.
    list_fn:
        Callable with signature
        ``(environment_name: str | None = None, **extra) ->
        tuple[Sequence[T], list[str]]``.
        ``extra`` keys are taken from ``extra_list_params``.
    get_fn:
        Callable with signature ``(ref: str) -> T | None``.
    get_param_name:
        The name of the string parameter passed to ``get_fn`` and exposed on
        the generated get tool, e.g. ``"app_ref"``, ``"task_id"``,
        ``"sandbox_ref"``.
    not_found_message_template:
        An f-string-style template with ``{ref}`` as the placeholder, e.g.
        ``"app not found: {ref}"``.
    tags:
        Tag set forwarded to both generated tools, e.g. ``{"apps"}``.
    extra_list_params:
        Optional list of additional parameter names (beyond
        ``environment_name``) accepted by ``list_fn``.  Each extra param is
        exposed on the list tool as ``str | None = None``.
    """
    plural = _pluralise(entity_name)
    list_tool_name = f"modal_list_{plural}"
    get_tool_name = f"modal_get_{entity_name}"
    extra = extra_list_params or []

    # ------------------------------------------------------------------
    # List tool — always accepts environment_name; optional extras are
    # forwarded as str | None keyword arguments.
    # ------------------------------------------------------------------
    if not extra:

        @mcp.tool(
            name=list_tool_name,
            tags=tags,
            annotations=READ_ONLY_ANNOTATIONS,
        )
        def _list_tool(environment_name: str | None = None) -> ToolEnvelope[Page[T]]:  # type: ignore[type-var]
            items, warnings = list_fn(environment_name)
            return page_envelope_partial(items, warnings)

    elif len(extra) == 1:
        extra_param = extra[0]
        _build_list_fn_one_extra(mcp, list_tool_name, tags, list_fn, extra_param)

    else:
        raise ValueError(
            f"register_read_toolset supports 0 or 1 extra_list_params; "
            f"got {len(extra)}.  Register the list tool manually."
        )

    # ------------------------------------------------------------------
    # Get tool — accepts a single string ref + optional environment_name.
    # environment_name is accepted for parity with adapter signatures that
    # include it, but not all adapters use it.
    # ------------------------------------------------------------------
    _build_get_fn(mcp, get_tool_name, tags, get_fn, get_param_name, not_found_message_template)


def _build_list_fn_one_extra(
    mcp: FastMCP[Any],
    list_tool_name: str,
    tags: set[str],
    list_fn: Callable[..., tuple[Sequence[Any], list[str]]],
    extra_param: str,
) -> None:
    """Register the list tool with one extra optional str parameter.

    Uses exec so FastMCP's inspection sees a function with the real parameter
    name (not **kwargs), which is required for correct schema generation.
    """
    fn_src = f"""
def _list_tool(environment_name: str | None = None, {extra_param}: str | None = None):
    items, warnings = list_fn(environment_name, **{{{repr(extra_param)}: {extra_param}}})
    return page_envelope_partial(items, warnings)
"""
    ns: dict[str, Any] = {
        "list_fn": list_fn,
        "page_envelope_partial": page_envelope_partial,
    }
    exec(fn_src, ns)  # noqa: S102
    fn = ns["_list_tool"]
    mcp.tool(name=list_tool_name, tags=tags, annotations=READ_ONLY_ANNOTATIONS)(fn)


def _build_get_fn(
    mcp: FastMCP[Any],
    get_tool_name: str,
    tags: set[str],
    get_fn: Callable[[str], Any],
    get_param_name: str,
    not_found_message_template: str,
) -> None:
    """Register the get tool with a dynamically-named ref parameter."""
    fn_src = f"""
def _get_tool({get_param_name}: str):
    result = get_fn({get_param_name})
    if result is None:
        return not_found(not_found_message_template.format(ref={get_param_name}))
    return envelope(result)
"""
    ns: dict[str, Any] = {
        "get_fn": get_fn,
        "not_found": not_found,
        "envelope": envelope,
        "not_found_message_template": not_found_message_template,
    }
    exec(fn_src, ns)  # noqa: S102
    fn = ns["_get_tool"]
    mcp.tool(name=get_tool_name, tags=tags, annotations=READ_ONLY_ANNOTATIONS)(fn)


__all__ = [
    "MUTATING_ANNOTATIONS",
    "READ_ONLY_ANNOTATIONS",
    "REQUEST_ID",
    "disabled_error",
    "envelope",
    "not_found",
    "page_envelope",
    "page_envelope_partial",
    "register_read_toolset",
]
```

- [ ] Run unit tests to verify green:

```bash
uv run pytest tests/unit/test_register_read_toolset.py -v
```

All 7 tests should pass.

- [ ] Run ruff on `_common.py`:

```bash
uv run ruff check src/modal_mcp/toolsets/_common.py
```

---

### Phase 3 — Migrate `apps.py`

The `modal_list_apps` / `modal_get_app` pair maps directly to the factory. `modal_list_app_deployments` stays custom (different shape).

- [ ] Replace `apps.py` with:

```python
"""App and deployment read-only tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import App, Deployment, Page
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    page_envelope,
    register_read_toolset,
)


def register_app_tools(mcp: FastMCP[Any]) -> None:
    """Register app tools with read-only annotations.

    list/get are handled by register_read_toolset.
    modal_list_app_deployments keeps custom registration: it takes app_ref as
    a required positional string, not the standard optional ref pattern.
    """
    register_read_toolset(
        mcp=mcp,
        entity_name="app",
        list_fn=lambda environment_name=None: get_modal_adapter().list_apps(environment_name),
        get_fn=lambda app_ref: get_modal_adapter().get_app(app_ref),
        get_param_name="app_ref",
        not_found_message_template="app not found: {ref}",
        tags={"apps"},
    )

    @mcp.tool(
        name="modal_list_app_deployments",
        tags={"apps"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_app_deployments(
        app_ref: str,
        environment_name: str | None = None,
    ) -> ToolEnvelope[Page[Deployment]]:
        return page_envelope(
            get_modal_adapter().list_app_deployments(app_ref, environment_name)
        )


__all__ = ["register_app_tools"]
```

- [ ] Verify integration tests still pass:

```bash
uv run pytest tests/integration/test_http_mcp.py -v -k "app"
```

---

### Phase 4 — Migrate `containers.py`

`modal_list_containers` takes an extra `app_ref` param; `modal_get_container` uses `task_id`. Both map to the factory. `modal_get_container_logs` keeps custom registration (unique time-range params + empty-log hint).

- [ ] Replace `containers.py` with:

```python
"""Container read-only tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastmcp import FastMCP

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope, ok
from modal_mcp.domain.models import Container, LogsPage, Page
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    REQUEST_ID,
    register_read_toolset,
)


def register_container_tools(mcp: FastMCP[Any]) -> None:
    """Register container tools with read-only annotations.

    list/get are handled by register_read_toolset.
    modal_get_container_logs keeps custom registration: unique time-range
    params, source/filter params, and an empty-log hint that references the
    task_id in a formatted warning message.
    """
    register_read_toolset(
        mcp=mcp,
        entity_name="container",
        list_fn=lambda environment_name=None, app_ref=None: (
            get_modal_adapter().list_containers(environment_name, app_ref)
        ),
        get_fn=lambda task_id: get_modal_adapter().get_container(task_id),
        get_param_name="task_id",
        not_found_message_template="container not found: {ref}",
        tags={"containers"},
        extra_list_params=["app_ref"],
    )

    @mcp.tool(
        name="modal_get_container_logs",
        tags={"containers", "logs"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_container_logs(
        task_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = 200,
        source: str | None = None,
        function_id: str | None = None,
        function_call_id: str | None = None,
        sandbox_id: str | None = None,
        search_text: str | None = None,
    ) -> ToolEnvelope[LogsPage]:
        result = get_modal_adapter().get_container_logs(
            task_id,
            since=since,
            until=until,
            limit=limit,
            source=source,
            function_id=function_id,
            function_call_id=function_call_id,
            sandbox_id=sandbox_id,
            search_text=search_text,
        )
        warnings: list[str] = []
        if not result.entries:
            warnings.append(
                f"Zero log entries returned for task_id={task_id!r}. "
                "Possible reasons: (1) container has expired, "
                "(2) time range did not match activity window, "
                "(3) logs not yet ingested. "
                "Try modal_get_app_logs with app_ref and task_id filter for a broader search."  # noqa: E501
            )
        return ok(result, request_id=REQUEST_ID, warnings=warnings)


__all__ = ["register_container_tools"]
```

- [ ] Run integration tests:

```bash
uv run pytest tests/integration/test_http_mcp.py -v -k "container"
```

---

### Phase 5 — Migrate `sandboxes.py`

`modal_list_sandboxes` / `modal_get_sandbox` map to the factory. `modal_get_sandbox_stdio` keeps custom registration (bounded-buffer truncation logic).

Note: `modal_list_sandboxes` has `include_finished: bool = False` as a third parameter beyond `environment_name` and `app_ref`. This is a bool, not `str | None`, so the factory's `extra_list_params` mechanism (which only supports `str | None` extras) cannot be used for this one parameter. Instead, we use an inline lambda that closes over the adapter and registers the list tool directly, while still using the factory's get-tool half via a partial approach.

Because the factory does not support bool extra params, we will register `modal_list_sandboxes` manually and use the factory only for `modal_get_sandbox`. This is consistent with the scope boundary (the ticket requires "at least 3 of the 5 entity toolsets use the factory for list/get"). We still eliminate the get-tool boilerplate.

> **Alternative:** If the agent decides to extend the factory to support bool params in a follow-up, that is a separate ticket. For this ticket, the priority is correctness over completeness.

- [ ] Replace `sandboxes.py` with:

```python
"""Sandbox read-only tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import Page, SandboxSummary
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    envelope,
    page_envelope,
    register_read_toolset,
)


class SandboxStdio(BaseModel):
    """Bounded sandbox stdio payload."""

    stdout: str
    stderr: str
    truncated: bool


def register_sandbox_tools(mcp: FastMCP[Any]) -> None:
    """Register sandbox tools with read-only annotations.

    modal_get_sandbox uses register_read_toolset for the standard get pattern.

    modal_list_sandboxes keeps custom registration: it has a bool parameter
    (include_finished) which is outside the str|None extra_list_params contract
    of register_read_toolset. See docs/plans/2026-05-14-dry-toolset-registration.md
    scope boundary notes.

    modal_get_sandbox_stdio keeps custom registration: unique bounded-buffer
    truncation logic and a tail_bytes parameter.
    """
    # list_sandboxes: custom — bool param not supported by factory
    @mcp.tool(
        name="modal_list_sandboxes",
        tags={"sandboxes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_sandboxes(
        environment_name: str | None = None,
        app_ref: str | None = None,
        include_finished: bool = False,
    ) -> ToolEnvelope[Page[SandboxSummary]]:
        return page_envelope(
            get_modal_adapter().list_sandboxes(
                environment_name,
                app_ref,
                include_finished=include_finished,
            )
        )

    # get_sandbox: uses factory
    register_read_toolset(
        mcp=mcp,
        entity_name="sandbox",
        list_fn=lambda environment_name=None: ([], []),  # dummy; list registered above
        get_fn=lambda sandbox_ref: get_modal_adapter().get_sandbox(sandbox_ref),
        get_param_name="sandbox_ref",
        not_found_message_template="sandbox not found: {ref}",
        tags={"sandboxes"},
    )
    # Remove the spurious modal_list_sandboxes that the factory just registered
    # (we passed a dummy list_fn). FastMCP allows re-registering; the manually
    # registered one above wins because it was registered first and FastMCP
    # raises on duplicate names. To avoid that, we use a sentinel name trick:

    # NOTE: The above approach will cause a duplicate tool name error. Use the
    # split helper instead (see _register_get_only below).


__all__ = ["SandboxStdio", "register_sandbox_tools"]
```

Wait — the factory always registers both tools. We cannot cleanly call the factory for only the get tool without also registering a duplicate list tool. We have two options:

**Option A (preferred):** Extend the factory to support a `get_only=True` flag that skips list registration.

**Option B:** Accept that sandboxes registers both tools via the factory, and pass through `include_finished` by always defaulting it to `False` at the factory boundary (losing the bool param from the schema).

Given the ticket's acceptance criterion is "at least 3 of 5 entity toolsets," and apps, containers, and discovery already qualify, we do NOT need to use the factory for sandboxes at all. The cleanest approach is to leave `sandboxes.py` unchanged from the current code and document why.

- [ ] Replace `sandboxes.py` back to its original unchanged form and add a scope comment:

```python
"""Sandbox read-only tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import Page, SandboxSummary
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    envelope,
    not_found,
    page_envelope,
)


class SandboxStdio(BaseModel):
    """Bounded sandbox stdio payload."""

    stdout: str
    stderr: str
    truncated: bool


def register_sandbox_tools(mcp: FastMCP[Any]) -> None:
    """Register sandbox tools with read-only annotations.

    modal_list_sandboxes: custom registration — list tool takes a bool param
    (include_finished) which is outside the str|None contract of
    register_read_toolset.  Extending the factory for bool params is deferred
    to a follow-up ticket.

    modal_get_sandbox: custom registration — factory cannot be called for the
    get tool in isolation without registering a duplicate list tool; kept
    custom to avoid complexity.

    modal_get_sandbox_stdio: custom registration — bounded-buffer truncation
    logic unique to this tool.
    """

    @mcp.tool(
        name="modal_list_sandboxes",
        tags={"sandboxes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_sandboxes(
        environment_name: str | None = None,
        app_ref: str | None = None,
        include_finished: bool = False,
    ) -> ToolEnvelope[Page[SandboxSummary]]:
        return page_envelope(
            get_modal_adapter().list_sandboxes(
                environment_name,
                app_ref,
                include_finished=include_finished,
            )
        )

    @mcp.tool(
        name="modal_get_sandbox",
        tags={"sandboxes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_sandbox(sandbox_ref: str) -> ToolEnvelope[SandboxSummary]:
        sandbox = get_modal_adapter().get_sandbox(sandbox_ref)
        if sandbox is None:
            return not_found(f"sandbox not found: {sandbox_ref}")
        return envelope(sandbox)

    @mcp.tool(
        name="modal_get_sandbox_stdio",
        tags={"sandboxes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_get_sandbox_stdio(
        sandbox_ref: str,
        tail_bytes: Annotated[int, Field(ge=1, le=65_536)] = 8_192,
    ) -> ToolEnvelope[SandboxStdio]:
        stdout, stderr = get_modal_adapter().get_sandbox_stdio(sandbox_ref)
        stdout_bytes = stdout.encode("utf-8")
        stderr_bytes = stderr.encode("utf-8")
        stdout_truncated = len(stdout_bytes) > tail_bytes
        stderr_truncated = len(stderr_bytes) > tail_bytes
        if stdout_truncated:
            stdout = stdout_bytes[-tail_bytes:].decode("utf-8", errors="replace")
        if stderr_truncated:
            stderr = stderr_bytes[-tail_bytes:].decode("utf-8", errors="replace")
        truncated = stdout_truncated or stderr_truncated
        return envelope(SandboxStdio(stdout=stdout, stderr=stderr, truncated=truncated))


__all__ = ["SandboxStdio", "register_sandbox_tools"]
```

---

### Phase 6 — Migrate `discovery.py` (`list_environments` / `get_environment`)

`discovery.py` contains five tools. `modal_list_environments` / `modal_get_environment` fit the factory pattern exactly. The three others (`modal_discovery_server_info`, `modal_whoami`, `modal_list_workspaces`) stay custom.

Note: `modal_get_environment` uses `environment_name` as its ref parameter (not a generic `*_ref`). The factory supports any `get_param_name` string, so this works cleanly.

- [ ] Replace `discovery.py` with:

```python
"""Discovery and workspace read-only tools."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.config import AuthMode, Settings
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import Environment, Page, Workspace
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    envelope,
    page_envelope,
    register_read_toolset,
)


class ServerInfo(BaseModel):
    """Fixed-schema model-visible server metadata."""

    model_config = ConfigDict(extra="forbid")

    mode: AuthMode
    read_only: bool
    toolsets: tuple[str, ...]
    version: str
    protocol_version: Literal["2025-06-18"] = "2025-06-18"


def register_discovery_tools(mcp: FastMCP[Any], settings: Settings) -> None:
    """Register discovery tools with read-only annotations.

    modal_list_environments / modal_get_environment use register_read_toolset.

    modal_discovery_server_info, modal_whoami, modal_list_workspaces keep
    custom registration: they have no environment_name param and do not follow
    the list/get pattern.
    """

    @mcp.tool(
        name="modal_discovery_server_info",
        tags={"discovery"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_discovery_server_info() -> ToolEnvelope[ServerInfo]:
        return envelope(
            ServerInfo(
                mode=settings.modal_mcp_auth_mode,
                read_only=settings.modal_mcp_read_only,
                toolsets=tuple(sorted(settings.modal_mcp_enabled_toolsets)),
                version=_package_version(),
            )
        )

    @mcp.tool(
        name="modal_whoami",
        tags={"discovery"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_whoami() -> ToolEnvelope[Workspace]:
        return envelope(get_modal_adapter().whoami())

    @mcp.tool(
        name="modal_list_workspaces",
        tags={"discovery"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_workspaces() -> ToolEnvelope[Page[Workspace]]:
        return page_envelope(get_modal_adapter().list_workspaces())

    register_read_toolset(
        mcp=mcp,
        entity_name="environment",
        list_fn=lambda environment_name=None: get_modal_adapter().list_environments(),
        get_fn=lambda environment_name: get_modal_adapter().get_environment(environment_name),
        get_param_name="environment_name",
        not_found_message_template="environment not found: {ref}",
        tags={"discovery"},
    )


def _package_version() -> str:
    try:
        return version("modal-mcp")
    except PackageNotFoundError:
        return "0.1.0"


__all__ = ["ServerInfo", "register_discovery_tools"]
```

**Important note for discovery:** The original `modal_list_environments` ignores the `environment_name` parameter (calls `list_environments()` with no args). The lambda passed to `list_fn` must preserve this: `lambda environment_name=None: get_modal_adapter().list_environments()`. The `environment_name` parameter is still exposed on the tool schema for consistency with other list tools, but the adapter does not consume it — matching existing behavior exactly.

- [ ] Verify integration tests:

```bash
uv run pytest tests/integration/test_http_mcp.py -v -k "environment"
```

---

### Phase 7 — Migrate `volumes.py` (`modal_list_volumes` only)

`volumes.py` has four tools. Only `modal_list_volumes` fits the exact factory pattern — it takes `environment_name` and returns `(items, warnings)`. However, the current code uses `page_envelope` (not `page_envelope_partial`) because `list_volumes` returns `list[VolumeSummary]` directly (no warnings tuple). This is inconsistent with the factory contract which expects `tuple[Sequence[T], list[str]]`.

We have two options:

1. Wrap the adapter call in a lambda that adds an empty warnings list: `lambda environment_name=None: (get_modal_adapter().list_volumes(environment_name), [])`.
2. Leave `volumes.py` unchanged (sandboxes and volumes both have non-standard shapes).

Since apps, containers, and discovery already satisfy "at least 3 of the 5" criterion, and volumes has no clean list/get pair (there is no `modal_get_volume` — the get-equivalent is `modal_ls_volume` with a different shape), **we leave `volumes.py` unchanged** and add only a scope comment.

- [ ] Add a scope-boundary comment to `volumes.py` above `register_volume_tools`:

```python
def register_volume_tools(mcp: FastMCP[Any]) -> None:
    """Register volume tools with read-only annotations.

    All four tools keep custom registration:
    • modal_list_volumes: adapter returns list[VolumeSummary] (no warnings
      tuple), so it does not match the register_read_toolset list_fn contract.
    • modal_ls_volume, modal_read_volume_text, modal_stat_volume_path: volume-
      path params outside the standard list/get pattern (scope boundary).
    """
```

- [ ] Edit only the docstring, leave all function bodies unchanged. Final `volumes.py`:

```python
"""Volume read-only tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import Page, VolumeEntry, VolumeSummary
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    envelope,
    not_found,
    page_envelope,
)


class VolumeText(BaseModel):
    """Text file payload returned from a volume."""

    content: str
    truncated: bool


def register_volume_tools(mcp: FastMCP[Any]) -> None:
    """Register volume tools with read-only annotations.

    All four tools keep custom registration:
    • modal_list_volumes: adapter returns list[VolumeSummary] (no warnings
      tuple), so it does not match the register_read_toolset list_fn contract.
    • modal_ls_volume, modal_read_volume_text, modal_stat_volume_path: volume-
      path params outside the standard list/get pattern (scope boundary).
    """

    @mcp.tool(
        name="modal_list_volumes",
        tags={"volumes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_volumes(
        environment_name: str | None = None,
    ) -> ToolEnvelope[Page[VolumeSummary]]:
        return page_envelope(get_modal_adapter().list_volumes(environment_name))

    @mcp.tool(
        name="modal_ls_volume",
        tags={"volumes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_ls_volume(
        volume_ref: str,
        path: str = "/",
        recursive: bool = False,
        max_entries: int | None = None,
    ) -> ToolEnvelope[Page[VolumeEntry]]:
        return page_envelope(
            get_modal_adapter().ls_volume(
                volume_ref,
                path,
                recursive=recursive,
                max_entries=max_entries,
            )
        )

    @mcp.tool(
        name="modal_read_volume_text",
        tags={"volumes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_read_volume_text(
        volume_ref: str,
        path: str,
        max_bytes: Annotated[int, Field(ge=1, le=1_048_576)] = 262_144,
    ) -> ToolEnvelope[VolumeText]:
        raw_content = get_modal_adapter().read_volume_text(
            volume_ref,
            path,
            max_bytes=max_bytes,
        )
        encoded = raw_content.encode("utf-8")
        truncated = len(encoded) > max_bytes
        content = encoded[:max_bytes].decode("utf-8", errors="replace")
        return envelope(VolumeText(content=content, truncated=truncated))

    @mcp.tool(
        name="modal_stat_volume_path",
        tags={"volumes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_stat_volume_path(
        volume_ref: str,
        path: str,
    ) -> ToolEnvelope[VolumeEntry]:
        entry = get_modal_adapter().stat_volume_path(volume_ref, path)
        if entry is None:
            return not_found(f"volume path not found: {path}")
        return envelope(entry)


__all__ = ["VolumeText", "register_volume_tools"]
```

---

### Phase 8 — Full test suite

- [ ] Run the full test suite:

```bash
uv run pytest -v 2>&1 | tail -40
```

All tests must pass.

- [ ] Run ruff across the whole codebase:

```bash
uv run ruff check .
```

Zero errors expected.

---

### Phase 9 — Verify acceptance criteria checklist

- [ ] Confirm `register_read_toolset` exists in `_common.py` and is exported in `__all__`.
- [ ] Confirm at least 3 of 5 entity toolsets use the factory: apps (list+get), containers (list+get), discovery (list_environments+get_environment). That is 3 entity files x 2 tools = 6 tools migrated.
- [ ] Verify tool names are identical to before migration by running:

```bash
uv run python3 -c "
from fastmcp import FastMCP
from modal_mcp.config import Settings
from pathlib import Path
import tempfile, os

with tempfile.NamedTemporaryFile(suffix='.toml', mode='w', delete=False) as f:
    f.write('[default]\n')
    cfg = f.name

settings = Settings(
    modal_config_path=Path(cfg),
    modal_mcp_allowed_origins=('http://127.0.0.1:8765',),
    modal_mcp_allowed_hosts=('127.0.0.1',),
    modal_mcp_signing_keys='kid1:' + 'a' * 64,
)
from modal_mcp.server import create_mcp
import asyncio
mcp = create_mcp(settings)
tools = asyncio.run(mcp.list_tools(run_middleware=False))
for t in sorted(tools, key=lambda x: x.name):
    print(t.name, '| tags:', t.tags, '| readOnly:', t.annotations.readOnlyHint)
os.unlink(cfg)
"
```

Confirm all expected tool names appear and all have `readOnly: True`.

- [ ] Commit:

```bash
git add \
  src/modal_mcp/toolsets/_common.py \
  src/modal_mcp/toolsets/apps.py \
  src/modal_mcp/toolsets/containers.py \
  src/modal_mcp/toolsets/discovery.py \
  src/modal_mcp/toolsets/sandboxes.py \
  src/modal_mcp/toolsets/volumes.py \
  tests/unit/test_register_read_toolset.py
git commit -m "refactor(toolsets): DRY up list/get registration with register_read_toolset factory

Extract register_read_toolset to _common.py. Migrate apps, containers,
discovery list/get tools to the factory (6 tools, 3 entity files).
Sandboxes and volumes keep custom registration with scope-boundary comments.
Adds 7 unit tests in test_register_read_toolset.py.

Closes epo-dry-up-toolset-registration-repl-cq9c"
```

---

## Self-Review

**Spec coverage:**

- factory in `_common.py` ✓
- at least 3 entity toolsets use factory ✓ (apps, containers, discovery)
- tool names, tags, annotations identical ✓ (verified by acceptance criteria step)
- `page_envelope_partial` and `not_found` used by factory ✓
- integration tests pass unchanged ✓
- scope boundary documented in code ✓

**Placeholder scan:** No `TODO`, `...`, `pass`, or `raise NotImplementedError` left in production code. The placeholder in the Phase 5 sandbox exploration was superseded by the decision to leave sandboxes unchanged.

**Type consistency:** `TypeVar T` is used in the factory signature. The `exec`-based approach for dynamic parameter naming produces functions without type annotations, which is acceptable since FastMCP inspects via `inspect.signature` at registration time, not type-checkers. `# type: ignore` comments are applied narrowly.

**Edge cases covered:**

- `extra_list_params=None` (default, 0 extras) → apps, discovery, base test
- `extra_list_params=["app_ref"]` (1 extra, str|None) → containers
- `extra_list_params` with 2+ items → raises `ValueError` with a clear message
- `get_fn` returns `None` → `not_found` with formatted message
- `environment_name` forwarded correctly to list_fn lambda
