# Delete change.py Stub Toolset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete change.py (the four `disabled_error()` mutating stubs), remove its registration call sites, and regenerate the schema snapshot so those tools no longer appear in tools/list.

**Architecture:** `change.py` registers `modal_stop_app`, `modal_rollback_app`, `modal_stop_container`, `modal_terminate_sandbox` under the "change" tag. `expert.py` is intentionally retained — its tools have a detailed v3 implementation spec (§10.4 of the v2 spec) and serve as schema-shape signals. The "change" tag and name are removed from server.py and **init**.py; the "expert" tag and everything in policy/rules.py, doctor.py, and engine.py that references "expert" remains untouched.

**Tech Stack:** Python 3.12+, FastMCP, pytest, uv, ruff

---

## File Structure

| Path | Action | What changes |
|---|---|---|
| `src/modal_mcp/toolsets/change.py` | **DELETE** | Entire file removed |
| `src/modal_mcp/toolsets/expert.py` | **NO CHANGE** | Retained — v3 roadmap signal |
| `src/modal_mcp/toolsets/__init__.py` | **MODIFY** | Remove change import and registration call only |
| `src/modal_mcp/server.py` | **MODIFY** | Remove `"change"` from ALL_TOOLSETS; update read-only guard to keep only `"expert"` |
| `src/modal_mcp/policy/engine.py` | **NO CHANGE** | MUTATING_TOOLS and classify_tool expert branch both retained |
| `tests/unit/test_toolsets.py` | **MODIFY** | Delete change test and its import only; expert test stays |
| `schema/mcp-tools.v1.json` | **REGENERATE** | Run `uv run python scripts/generate_schemas.py` |

### Files NOT changed (confirmed)

- `src/modal_mcp/toolsets/_common.py` — `disabled_error()` helper retained
- `src/modal_mcp/toolsets/expert.py` — v3 stub retained
- `src/modal_mcp/policy/engine.py` — MUTATING_TOOLS, classify_tool expert branch all unchanged
- `src/modal_mcp/policy/rules.py` — `CHANGE_TOOLSETS = frozenset({"change", "expert"})` retained
- `src/modal_mcp/doctor.py` — `_MUTATING_TOOLSETS = frozenset({"change", "expert"})` retained
- `tests/unit/test_policy.py` — no changes
- `tests/integration/test_http_mcp.py` — no changes

---

## Tasks

### Step 1 — Confirm the test suite is green before touching anything

- [ ] Run `uv run pytest --tb=short -q` from the repo root and confirm zero failures. If failures exist, stop and report them — do not proceed.

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest --tb=short -q
```

---

### Step 2 — Delete change.py

- [ ] Delete the file `src/modal_mcp/toolsets/change.py`.

```bash
rm src/modal_mcp/toolsets/change.py
```

---

### Step 3 — Remove change registration from toolsets/**init**.py

- [ ] Edit `src/modal_mcp/toolsets/__init__.py`. Remove only the change import line and its registration call. The expert import and call stay. Final file:

```python
"""FastMCP toolset registration for Modal MCP."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from modal_mcp.config import Settings
from modal_mcp.toolsets.apps import register_app_tools
from modal_mcp.toolsets.containers import register_container_tools
from modal_mcp.toolsets.discovery import register_discovery_tools
from modal_mcp.toolsets.expert import register_expert_tools
from modal_mcp.toolsets.logs import register_log_tools
from modal_mcp.toolsets.sandboxes import register_sandbox_tools
from modal_mcp.toolsets.volumes import register_volume_tools


def register_toolsets(mcp: FastMCP[Any], settings: Settings) -> None:
    """Register all v1 toolsets before policy disables unavailable tags."""

    register_discovery_tools(mcp, settings)
    register_app_tools(mcp)
    register_log_tools(mcp)
    register_container_tools(mcp)
    register_volume_tools(mcp)
    register_sandbox_tools(mcp)
    register_expert_tools(mcp)


__all__ = ["register_toolsets"]
```

---

### Step 4 — Remove "change" from ALL_TOOLSETS in server.py

- [ ] Edit `src/modal_mcp/server.py`. Change the `ALL_TOOLSETS` set from:

```python
ALL_TOOLSETS = frozenset(
    {
        "discovery",
        "apps",
        "containers",
        "logs",
        "volumes",
        "sandboxes",
        "change",
        "expert",
    }
)
```

to:

```python
ALL_TOOLSETS = frozenset(
    {
        "discovery",
        "apps",
        "containers",
        "logs",
        "volumes",
        "sandboxes",
        "expert",
    }
)
```

---

### Step 5 — Update the read-only guard in server.py

- [ ] Edit `src/modal_mcp/server.py`. The read-only guard currently disables both tags:

```python
    if resolved_settings.modal_mcp_read_only:
        mcp.disable(tags={"change", "expert"})
```

Change to disable only `"expert"` (no change tools exist to disable):

```python
    if resolved_settings.modal_mcp_read_only:
        mcp.disable(tags={"expert"})
```

---

### Step 6 — Delete change test from tests/unit/test_toolsets.py

- [ ] Edit `tests/unit/test_toolsets.py`. Remove:
  - The import `from modal_mcp.toolsets.change import register_change_tools`
  - The entire `test_change_stubs_return_disabled_capability_errors` test function (lines 18–38)

Keep all other content unchanged: `import pytest`, the expert import, the expert test, and the discovery test. Final file:

```python
"""Unit tests for toolset registration helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import FastMCP
from pydantic import SecretStr

from modal_mcp.config import Settings
from modal_mcp.toolsets.discovery import register_discovery_tools
from modal_mcp.toolsets.expert import register_expert_tools


@pytest.mark.asyncio
async def test_expert_execute_stub_returns_disabled_capability_error() -> None:
    """Expert stubs stay explicit and disabled for v1."""

    mcp: FastMCP[None] = FastMCP("test")
    register_expert_tools(mcp)

    result = await mcp.call_tool(
        "modal_expert_execute",
        {"plan": {"steps": []}, "dry_run": True, "approval_token": "mappr1.token"},
        run_middleware=False,
    )

    payload = result.structured_content
    assert payload is not None
    assert payload["ok"] is False
    assert payload["error"]["code"] == "POLICY_BLOCKED"
    assert payload["error"]["details"]["submitted_plan"] == {"steps": []}


@pytest.mark.asyncio
async def test_modal_discovery_server_info_returns_hosted_read_only_mode(
    tmp_path: Path,
) -> None:
    """Discovery output uses the canonical hosted mode string for hosted auth."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    settings = Settings(
        modal_config_path=modal_config,
        modal_mcp_auth_mode="hosted_oauth",
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_public_origin="https://mcp.example.com",
        modal_mcp_auth_issuer="https://issuer.example.com",
        modal_mcp_auth_jwks_uri="https://issuer.example.com/jwks.json",
        modal_mcp_auth_audience="modal-mcp",
        modal_mcp_allowed_redirect_uris=("https://client.example.com/cb",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
    )

    mcp = FastMCP("test")
    register_discovery_tools(mcp, settings)

    result = await mcp.call_tool(
        "modal_discovery_server_info", {}, run_middleware=False
    )

    payload = result.structured_content
    assert payload is not None
    assert payload["ok"] is True
    assert payload["data"]["mode"] == "hosted_read_only_ephemeral"
```

---

### Step 7 — Verify no import errors and ruff passes

- [ ] Run ruff to confirm no lint errors introduced:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run ruff check .
```

Fix any unused-import (`F401`) or other errors reported before continuing.

---

### Step 8 — Regenerate the schema snapshot

- [ ] Run the schema generator to update `schema/mcp-tools.v1.json`:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run python scripts/generate_schemas.py
```

This overwrites `schema/mcp-tools.v1.json` in place. Confirm the command exits 0.

---

### Step 9 — Verify schema no longer contains change stub tools

- [ ] Confirm the change stub names are absent and log the remaining tool count:

```bash
python3 -c "
import json
data = json.load(open('schema/mcp-tools.v1.json'))
names = {t['name'] for t in data['tools']}
stubs = {'modal_stop_app', 'modal_rollback_app', 'modal_stop_container', 'modal_terminate_sandbox'}
found = names & stubs
assert not found, f'Change stubs still in schema: {found}'
# Expert tools should still be present
expert = {'modal_expert_search', 'modal_expert_execute'}
missing_expert = expert - names
assert not missing_expert, f'Expert tools unexpectedly gone: {missing_expert}'
print(f'OK: {len(names)} tools present; change stubs gone; expert tools retained')
"
```

---

### Step 10 — Run the full test suite

- [ ] Run all tests and confirm zero failures:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest --tb=short -q
```

Key tests that must pass:

- `tests/contract/test_schemas.py::test_schema_snapshot_is_current`
- `tests/unit/test_toolsets.py` — expert test and discovery test both pass
- `tests/unit/test_policy.py` — all policy tests pass unchanged
- `tests/unit/test_doctor.py` — doctor checks toolset names from env vars; passes unchanged
- `tests/integration/test_http_mcp.py` — approval flow and tools/list tests pass

---

### Step 11 — Commit

- [ ] Stage and commit the deletions and modifications:

```bash
cd "$(git rev-parse --show-toplevel)" && git add \
  src/modal_mcp/toolsets/change.py \
  src/modal_mcp/toolsets/__init__.py \
  src/modal_mcp/server.py \
  tests/unit/test_toolsets.py \
  schema/mcp-tools.v1.json
git commit -m "feat(toolsets): delete change.py stub toolset

Remove 4 disabled_error() stubs that appeared in tools/list without
doing anything. Expert.py retained — modal_expert_search and
modal_expert_execute are v3 roadmap signals per spec §10.4.
Regenerate schema snapshot."
```

---

## Self-review checklist

- [x] **Scope**: Only change.py deleted. expert.py untouched.
- [x] **Policy engine untouched**: MUTATING_TOOLS, classify_tool expert branch, CHANGE_TOOLSETS,_MUTATING_TOOLSETS all unchanged.
- [x] **Expert tests retained**: `test_expert_execute_stub_returns_disabled_capability_error` kept in test_toolsets.py.
- [x] **Schema verification**: Step 9 asserts change stubs gone AND expert tools still present.
- [x] **No placeholders**: Every step shows exact file content or exact commands.
- [x] **Blast radius confirmed**: policy/rules.py, doctor.py, integration tests all explicitly identified as untouched.
