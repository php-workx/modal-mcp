# PolicyContext: Concentrate Policy Dependency Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `PolicyMiddleware.__init__`'s 8-keyword-argument waterfall (each with a lazy `_*_from_settings()` default) with a single `PolicyContext` adapter built once at server startup. After this change `PolicyMiddleware.__init__` takes exactly three positional arguments — `mcp`, `context`, `settings` — and the four `_*_from_settings()` helpers inside `engine.py` are deleted.

**Architecture:** A new frozen dataclass `PolicyContext` (in `src/modal_mcp/policy/context.py`) bundles the seven pre-bound policy dependencies that `PolicyMiddleware` currently receives lazily: `approval_ledger`, `rate_limiter`, `mutation_limiter`, `actor_resolver`, `audit_sink`, `signing_keys`, `now`. A single `classmethod` `from_settings(settings)` is the only resolution path — misconfiguration (e.g. missing signing keys when `read_only=False`) fails at startup with a clean traceback to `PolicyContext.from_settings`, not on the first tool call. A new module `src/modal_mcp/policy/audit.py` extracts the existing `NullAuditSink` (currently inlined in `engine.py`) and promotes it to a typed `AuditSink` Protocol with three methods (`record_decision`, `record_error`, `record_result`). `server.py` constructs `PolicyContext.from_settings(resolved_settings)` once and passes both `context` and `resolved_settings` to `PolicyMiddleware`. Tests replace per-call kwarg wiring with a shared `policy_context` fixture.

**Tech Stack:** Python 3.12, dataclasses (`@dataclass(frozen=True, slots=True)`), `typing.Protocol`, FastMCP (`fastmcp.server.middleware.Middleware`), pytest, ruff.

---

## File Structure

Files touched by this plan (repo-relative paths only):

```text
src/modal_mcp/policy/context.py          ← NEW: PolicyContext dataclass + from_settings
src/modal_mcp/policy/audit.py            ← NEW: AuditSink Protocol + NullAuditSink (moved out of engine.py)
src/modal_mcp/policy/engine.py           ← shrink __init__ to (mcp, context, settings); delete 2 helpers; delete NullAuditSink
src/modal_mcp/server.py                  ← build PolicyContext.from_settings(...) once and pass to PolicyMiddleware
tests/unit/test_policy.py                ← add policy_context fixture; rewrite middleware constructions to use it
tests/unit/test_policy_context.py        ← NEW: PolicyContext.from_settings contract tests (incl. startup-failure invariant)
```

No public-API changes outside the policy package and `server.py`. The HTTP approval route (which uses `_approval_signing_keys_from_settings`, `_approval_rate_limiter_from_settings`, etc.) is **not** in scope — those helpers live in `server.py` and serve a different code path. This plan limits its blast radius to `PolicyMiddleware` construction.

---

## Step 1 — Write failing tests for `PolicyContext` and `AuditSink`

### 1a — Create `tests/unit/test_policy_context.py`

- [ ] Create the file with the following content:

```python
"""Unit tests for PolicyContext and AuditSink protocol."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from modal_mcp.config import ConfigError, Settings
from modal_mcp.policy.approval import ApprovalActor, ApprovalTokenLedger
from modal_mcp.policy.audit import AuditSink, NullAuditSink
from modal_mcp.policy.context import PolicyContext
from modal_mcp.policy.rate_limit import TokenBucketRateLimiter
from modal_mcp.policy.rules import CHANGE_TOOLSETS, READ_ONLY_TOOLSETS

_SIGNING_KEYS = "kid1:" + "a" * 64


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    base: dict[str, Any] = {
        "modal_config_path": modal_config,
        "modal_mcp_allowed_origins": ("http://127.0.0.1:8765",),
        "modal_mcp_allowed_hosts": ("127.0.0.1", "localhost"),
        "modal_mcp_signing_keys": SecretStr(_SIGNING_KEYS),
        "modal_mcp_read_only": True,
        "modal_mcp_enabled_toolsets": READ_ONLY_TOOLSETS | CHANGE_TOOLSETS,
        "modal_mcp_mutation_rate_limit_seconds": 0,
    }
    base.update(overrides)
    return Settings(**base)


class TestPolicyContextFromSettings:
    def test_returns_policy_context_instance(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        assert isinstance(ctx, PolicyContext)

    def test_binds_approval_ledger(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        assert isinstance(ctx.approval_ledger, ApprovalTokenLedger)

    def test_binds_rate_limiter(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        assert isinstance(ctx.rate_limiter, TokenBucketRateLimiter)

    def test_mutation_limiter_none_when_disabled(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(
            _settings(tmp_path, modal_mcp_mutation_rate_limit_seconds=0)
        )
        assert ctx.mutation_limiter is None

    def test_mutation_limiter_present_when_configured(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(
            _settings(tmp_path, modal_mcp_mutation_rate_limit_seconds=5)
        )
        assert isinstance(ctx.mutation_limiter, TokenBucketRateLimiter)

    def test_signing_keys_parsed_into_tuple(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        assert isinstance(ctx.signing_keys, tuple)
        assert len(ctx.signing_keys) >= 1
        kid, key = ctx.signing_keys[0]
        assert kid == "kid1"
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_now_is_callable_returning_int(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        now_value = ctx.now()
        assert isinstance(now_value, int)
        # within 5 seconds of wall clock
        assert abs(now_value - int(time.time())) < 5

    def test_audit_sink_implements_protocol(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        # Either NullAuditSink or JSONLAuditSink — both satisfy AuditSink.
        assert hasattr(ctx.audit_sink, "record_decision")
        assert hasattr(ctx.audit_sink, "record_error")
        assert hasattr(ctx.audit_sink, "record_result")

    def test_actor_resolver_is_callable(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        assert callable(ctx.actor_resolver)


class TestPolicyContextIsFrozen:
    def test_cannot_reassign_field(self, tmp_path: Path) -> None:
        ctx = PolicyContext.from_settings(_settings(tmp_path))
        with pytest.raises((AttributeError, TypeError)):
            ctx.approval_ledger = ApprovalTokenLedger(None)  # type: ignore[misc]


class TestPolicyContextStartupInvariants:
    """Misconfiguration must fail at PolicyContext.from_settings, not on the first tool call."""

    def test_raises_when_signing_keys_missing_and_not_read_only(
        self, tmp_path: Path
    ) -> None:
        # Construct Settings that pass startup validation, then mutate
        # signing_keys to None to simulate the deletion path.
        settings = _settings(tmp_path, modal_mcp_read_only=False)
        # Bypass model_validator post-construction: object.__setattr__ on
        # frozen=False BaseSettings is permitted.
        object.__setattr__(settings, "modal_mcp_signing_keys", None)

        with pytest.raises(ConfigError, match="signing keys"):
            PolicyContext.from_settings(settings)

    def test_allows_missing_signing_keys_when_read_only(self, tmp_path: Path) -> None:
        """Read-only mode never consumes approvals, so empty signing keys are tolerable."""

        settings = _settings(tmp_path, modal_mcp_read_only=True)
        object.__setattr__(settings, "modal_mcp_signing_keys", None)
        ctx = PolicyContext.from_settings(settings)
        assert ctx.signing_keys == ()


class TestNullAuditSink:
    def test_record_decision_is_noop(self) -> None:
        sink = NullAuditSink()
        sink.record_decision(object(), object())  # no exception

    def test_record_error_is_noop(self) -> None:
        sink = NullAuditSink()
        sink.record_error(object(), "tool", RuntimeError("boom"))

    def test_record_result_is_noop(self) -> None:
        sink = NullAuditSink()
        sink.record_result(object(), "tool", object())

    def test_satisfies_protocol_at_runtime(self) -> None:
        sink: AuditSink = NullAuditSink()  # static check via assignment
        assert sink is not None
```

- [ ] Run the new tests and confirm they **fail** with `ModuleNotFoundError`:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_policy_context.py -x 2>&1 | head -20
```

Expected error: `ModuleNotFoundError: No module named 'modal_mcp.policy.context'` (and `... 'modal_mcp.policy.audit'`).

---

## Step 2 — Implement `AuditSink` protocol in `src/modal_mcp/policy/audit.py`

- [ ] Create `src/modal_mcp/policy/audit.py` with this content:

```python
"""Audit-sink protocol for policy decisions and tool outcomes.

The protocol is intentionally narrow — three methods, each accepting
positional context plus event-specific arguments — so multiple concrete
implementations (NullAuditSink for tests, JSONLAuditSink for production)
can satisfy it without inheritance.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from fastmcp.tools.base import ToolResult


@runtime_checkable
class AuditSink(Protocol):
    """Receive policy decisions and tool outcomes for redacted audit logging."""

    def record_decision(self, context: Any, decision: Any) -> None:
        """Record a policy decision for a tool call."""

    def record_error(self, context: Any, tool_name: str, exc: Exception) -> None:
        """Record a redacted tool error."""

    def record_result(
        self, context: Any, tool_name: str, result: ToolResult
    ) -> None:
        """Record a redacted tool result summary."""


class NullAuditSink:
    """No-op audit hook used when no structured audit sink is configured."""

    def record_decision(self, *_: Any, **__: Any) -> None:
        return

    def record_error(self, *_: Any, **__: Any) -> None:
        return

    def record_result(self, *_: Any, **__: Any) -> None:
        return


__all__ = ["AuditSink", "NullAuditSink"]
```

- [ ] Verify the file parses and the protocol unit tests now pass:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_policy_context.py::TestNullAuditSink -v
```

Expected: all `TestNullAuditSink` tests pass.

---

## Step 3 — Implement `PolicyContext` in `src/modal_mcp/policy/context.py`

The dataclass concentrates every resolution step that the current `PolicyMiddleware.__init__` and the four `_*_from_settings()` helpers spread across `engine.py`. The `from_settings` classmethod becomes the single resolution path.

- [ ] Create `src/modal_mcp/policy/context.py` with this content:

```python
"""Pre-bound policy dependencies built once at server startup."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Self

from modal_mcp.config import ConfigError, Settings
from modal_mcp.domain.refs import parse_signing_keys
from modal_mcp.observability.audit import audit_sink_from_settings
from modal_mcp.policy.approval import ApprovalActor, ApprovalTokenLedger
from modal_mcp.policy.audit import AuditSink, NullAuditSink
from modal_mcp.policy.rate_limit import TokenBucketRateLimiter

# Forward type alias matching engine.ActorResolver to avoid a circular import.
# engine.py imports PolicyContext (this module), not the other way around.
ActorResolver = Callable[[object], ApprovalActor]


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Pre-bound policy dependencies built once at server startup.

    Holding every dependency as a value (never a factory) makes
    ``PolicyMiddleware.__init__`` a three-argument assignment with zero
    branching.  Misconfiguration surfaces here, at startup, with a clean
    traceback to :meth:`from_settings` — not on the first tool call.
    """

    approval_ledger: ApprovalTokenLedger
    rate_limiter: TokenBucketRateLimiter
    mutation_limiter: TokenBucketRateLimiter | None
    actor_resolver: ActorResolver
    audit_sink: AuditSink
    signing_keys: tuple[tuple[str, bytes], ...]
    now: Callable[[], int]

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Build a fully-bound PolicyContext from validated Settings.

        Raises:
            ConfigError: when an invariant fails at startup
                (e.g. mutating mode without signing keys).
        """

        from modal_mcp.policy.engine import resolve_middleware_actor

        approval_ledger = ApprovalTokenLedger(settings.modal_mcp_approval_ledger)
        rate_limiter = TokenBucketRateLimiter(
            capacity=max(1.0, settings.modal_mcp_rate_limit_rps),
            refill_rate_per_second=settings.modal_mcp_rate_limit_rps,
        )
        mutation_limiter = _build_mutation_limiter(settings)
        signing_keys = _build_signing_keys(settings)
        audit_sink = audit_sink_from_settings(settings)
        return cls(
            approval_ledger=approval_ledger,
            rate_limiter=rate_limiter,
            mutation_limiter=mutation_limiter,
            actor_resolver=resolve_middleware_actor,
            audit_sink=audit_sink,
            signing_keys=signing_keys,
            now=lambda: int(time.time()),
        )


def _build_mutation_limiter(settings: Settings) -> TokenBucketRateLimiter | None:
    seconds = settings.modal_mcp_mutation_rate_limit_seconds
    if seconds <= 0:
        return None
    return TokenBucketRateLimiter(
        capacity=1.0,
        refill_rate_per_second=1.0 / seconds,
    )


def _build_signing_keys(settings: Settings) -> tuple[tuple[str, bytes], ...]:
    raw_keys = settings.modal_mcp_signing_keys
    if raw_keys is None:
        if not settings.modal_mcp_read_only:
            msg = (
                "policy context: signing keys are required when "
                "MODAL_MCP_READ_ONLY=false (mutating tools must be able to "
                "verify approval tokens at call time)"
            )
            raise ConfigError(msg)
        return ()
    try:
        parsed = parse_signing_keys(raw_keys.get_secret_value())
    except ValueError as exc:
        msg = "policy context: MODAL_MCP_SIGNING_KEYS is malformed"
        raise ConfigError(msg) from exc
    return tuple(parsed)


def _audit_sink_provides_protocol(sink: object) -> bool:
    """Light runtime check used by tests that don't import audit_sink_from_settings."""

    return (
        hasattr(sink, "record_decision")
        and hasattr(sink, "record_error")
        and hasattr(sink, "record_result")
    )


__all__ = [
    "ActorResolver",
    "NullAuditSink",  # re-exported so callers don't have to import audit.py
    "PolicyContext",
]
```

- [ ] Run the PolicyContext test class:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_policy_context.py -v
```

Expected: every test in `tests/unit/test_policy_context.py` passes.

If `TestPolicyContextStartupInvariants::test_raises_when_signing_keys_missing_and_not_read_only` fails because `object.__setattr__` is blocked, fall back to constructing `Settings` directly without the post-validator (the test demonstrates the **invariant**; bypassing validation is the safest reproduction technique).

---

## Step 4 — Refactor `PolicyMiddleware` to use `PolicyContext`

`PolicyMiddleware.__init__` shrinks from 8 keyword arguments to 3 positional arguments. The four `_*_from_settings()` helpers (`_default_mutation_limiter`, `_signing_keys_from_settings`) and the inline `NullAuditSink` class are deleted — their work has moved to `PolicyContext`.

### 4a — Rewrite imports and class body in `src/modal_mcp/policy/engine.py`

- [ ] Replace the top-of-file imports (current lines 1-26) with:

```python
"""FastMCP middleware for Modal MCP policy enforcement."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import mcp.types as mt
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from pydantic import BaseModel, ConfigDict, TypeAdapter

from modal_mcp.config import Settings
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.domain.refs import decode_approval
from modal_mcp.observability.redact import collect_known_secrets, redact_value
from modal_mcp.policy.approval import ApprovalActor
from modal_mcp.policy.audit import AuditSink, NullAuditSink
from modal_mcp.policy.context import PolicyContext
from modal_mcp.policy.rate_limit import rate_limit_key
from modal_mcp.policy.rules import CHANGE_TOOLSETS, PolicyDecision, evaluate
```

Notes:
- Drop `time`, `ApprovalTokenLedger`, `TokenBucketRateLimiter`, `parse_signing_keys` — all now resolved inside `PolicyContext`.
- Keep `ActorResolver` available via `policy.context` (re-export below) so adapter/test code that imports it from engine doesn't break.

- [ ] Just below the imports, keep the existing `MUTATING_TOOLS` frozenset and the local `ActorResolver` type alias. Replace lines 28-37 with:

```python
MUTATING_TOOLS = frozenset(
    {
        "modal_stop_app",
        "modal_rollback_app",
        "modal_stop_container",
        "modal_terminate_sandbox",
        "modal_expert_execute",
    }
)
ActorResolver = Callable[[MiddlewareContext[Any]], ApprovalActor]
```

- [ ] Delete the entire `class NullAuditSink:` block (current lines 58-68). The class now lives in `policy.audit`.

### 4b — Replace `PolicyMiddleware.__init__`

- [ ] Replace the entire `__init__` (current lines 74-102) with the three-argument form:

```python
class PolicyMiddleware(Middleware):
    """Apply Modal MCP policy to every FastMCP tool call."""

    def __init__(
        self,
        mcp: FastMCP[Any],
        context: PolicyContext,
        settings: Settings,
    ) -> None:
        self._mcp = mcp
        self._context = context
        self.settings = settings
        self.approval_ledger = context.approval_ledger
        self.rate_limiter = context.rate_limiter
        self.mutation_limiter = context.mutation_limiter
        self.actor_resolver = context.actor_resolver
        self.audit_sink: AuditSink = context.audit_sink
        self.signing_keys = context.signing_keys
        self.known_secrets = collect_known_secrets(settings)
        self._now = context.now
        self._argument_adapter = TypeAdapter(PolicyCallArguments)
```

Notes:
- Every former lazy default is now read directly from `context`.
- The public attribute names (`approval_ledger`, `rate_limiter`, `audit_sink`, etc.) are unchanged — `on_call_tool` and `_consume_approval` keep using `self.approval_ledger`, `self.signing_keys`, `self._now()` exactly as before.
- `self._context` is retained as a single canonical reference for future deepening (e.g. exposing it to nested helpers).

### 4c — Delete the dead helpers

- [ ] Delete `def _default_mutation_limiter(...)` (current lines 362-371).
- [ ] Delete `def _signing_keys_from_settings(...)` (current lines 374-378).

- [ ] Update `__all__` at the bottom (current lines 390-401) to remove `NullAuditSink` (it now lives in `policy.audit`):

```python
__all__ = [
    "MUTATING_TOOLS",
    "PolicyCallArguments",
    "PolicyMiddleware",
    "ToolPolicy",
    "extract_target_refs",
    "redact_tool_result",
    "redact_value",
    "resolve_mcp_session_id",
    "resolve_middleware_actor",
]
```

- [ ] If any test or other module imports `NullAuditSink` from `modal_mcp.policy.engine`, redirect to `modal_mcp.policy.audit`. Check and migrate:

```bash
cd "$(git rev-parse --show-toplevel)" && grep -rn "from modal_mcp.policy.engine import.*NullAuditSink\|policy.engine.NullAuditSink" src/ tests/
```

For each hit, change the import path to `from modal_mcp.policy.audit import NullAuditSink`.

### 4d — Run targeted tests

- [ ] Verify the engine module imports cleanly:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run python -c "from modal_mcp.policy.engine import PolicyMiddleware; print('ok')"
```

Expected output: `ok`.

- [ ] Run the policy-context tests once more:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_policy_context.py -v
```

Expected: green.

The middleware test suite will fail until step 5; that is expected.

---

## Step 5 — Update `tests/unit/test_policy.py` to use a `policy_context` fixture

Every existing call site builds `PolicyMiddleware(annotation_mcp, policy_settings, actor_resolver=lambda _: ApprovalActor("alice", "auth-1"))`. After step 4 the third positional argument is `settings`, not the `actor_resolver` kwarg, so each call site must be rewritten.

### 5a — Add the fixture

- [ ] In `tests/unit/test_policy.py`, immediately after the existing `policy_settings` fixture (current ends around line 103), insert:

```python
@pytest.fixture
def policy_context(policy_settings: Settings) -> PolicyContext:
    """PolicyContext built from policy_settings with a fixed actor resolver.

    Tests that need a deterministic actor identity reuse this fixture; tests
    that need a non-standard component (e.g. a custom rate limiter or a
    fixed-time `now`) build a small override via dataclasses.replace.
    """

    base = PolicyContext.from_settings(policy_settings)
    return replace(
        base,
        actor_resolver=lambda _: ApprovalActor("alice", "auth-1"),
        now=lambda: 1_006,
    )
```

- [ ] Add the supporting imports at the top of `tests/unit/test_policy.py`:

```python
from dataclasses import replace

from modal_mcp.policy.context import PolicyContext
```

### 5b — Rewrite each `PolicyMiddleware(...)` call

The current pattern (line 297-301 is the template) is:

```python
middleware = PolicyMiddleware(
    annotation_mcp,
    policy_settings,
    actor_resolver=lambda _: ApprovalActor("alice", "auth-1"),
)
```

Replace it with the three-argument form, threading the new fixture:

```python
middleware = PolicyMiddleware(
    annotation_mcp,
    policy_context,
    policy_settings,
)
```

- [ ] Update every `classify_tool` test (test_policy.py lines 291-413) by adding `policy_context: PolicyContext` to the signature and using the form above. Concretely, replace each block matching the template with:

  ```python
  middleware = PolicyMiddleware(annotation_mcp, policy_context, policy_settings)
  ```

  and add `policy_context: PolicyContext` to the function arguments.

- [ ] For middleware-flow tests that currently inject `approval_ledger=...` and `audit_sink=...` (search for the pattern):

```bash
cd "$(git rev-parse --show-toplevel)" && grep -n "PolicyMiddleware(" tests/unit/test_policy.py
```

  For each hit, build a derived context via `replace(...)`. Template:

```python
ledger = ApprovalTokenLedger(None, now=lambda: 1_006)
audit_sink = _RecordingAuditSink()  # whatever the test currently builds
ctx = replace(
    policy_context,
    approval_ledger=ledger,
    audit_sink=audit_sink,
    now=lambda: 1_006,
)
middleware = PolicyMiddleware(annotation_mcp, ctx, policy_settings)
```

  Tests that previously passed `signing_keys=(("kid1", _SIGNING_KEY_BYTES),)` should now build:

```python
ctx = replace(policy_context, signing_keys=(("kid1", _SIGNING_KEY_BYTES),))
middleware = PolicyMiddleware(middleware_mcp, ctx, policy_settings)
```

- [ ] Confirm no call site still uses the kwarg form:

```bash
cd "$(git rev-parse --show-toplevel)" && grep -n "PolicyMiddleware(" tests/unit/test_policy.py | grep -E "approval_ledger=|audit_sink=|actor_resolver=|signing_keys=|mutation_limiter=|rate_limiter=|now=" || echo "no stale kwargs"
```

Expected: `no stale kwargs`.

### 5c — Run the policy test suite

- [ ] Execute:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_policy.py -v
```

Expected: all tests pass. If a test fails because the test code constructed an `ApprovalTokenLedger` with a custom `now`, ensure the corresponding `replace(policy_context, now=...)` is threaded so the middleware and ledger see the same clock.

---

## Step 6 — Update `src/modal_mcp/server.py` to build PolicyContext once

The current `create_mcp` constructs `PolicyMiddleware(mcp, resolved_settings, approval_ledger=..., audit_sink=...)`. After this step it constructs the middleware with `PolicyContext.from_settings(resolved_settings)`. The injected `approval_ledger`/`audit_sink` keyword arguments to `create_mcp` are preserved for backward compatibility — when supplied they override the context.

### 6a — Add the import

- [ ] At the top of `src/modal_mcp/server.py`, add:

```python
from modal_mcp.policy.context import PolicyContext
```

### 6b — Replace the `PolicyMiddleware` construction

The current block (around lines 393-401) is:

```python
mcp.add_middleware(OtelMiddleware(resolved_settings))
mcp.add_middleware(
    PolicyMiddleware(
        mcp,
        resolved_settings,
        approval_ledger=approval_ledger,
        audit_sink=resolved_audit_sink,
    )
)
```

- [ ] Replace it with:

```python
mcp.add_middleware(OtelMiddleware(resolved_settings))

policy_context = PolicyContext.from_settings(resolved_settings)
if approval_ledger is not None:
    policy_context = replace(policy_context, approval_ledger=approval_ledger)
if resolved_audit_sink is not None:
    policy_context = replace(policy_context, audit_sink=resolved_audit_sink)

mcp.add_middleware(PolicyMiddleware(mcp, policy_context, resolved_settings))
```

- [ ] Add the import for `replace`:

```python
from dataclasses import replace
```

### 6c — Verify the import surface stays minimal

- [ ] Confirm `server.py` no longer reaches into engine helpers that were deleted:

```bash
cd "$(git rev-parse --show-toplevel)" && grep -n "_default_mutation_limiter\|_signing_keys_from_settings" src/modal_mcp/server.py
```

Expected: no output. (`server.py` already had its own `_approval_signing_keys_from_settings` for the HTTP approval route — that helper is unrelated and stays.)

### 6d — Smoke-test the server factory

- [ ] Run the server-construction tests:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_server.py -v
```

Expected: all tests pass. If a test stubbed `audit_sink_from_settings` to verify a specific sink lands in the middleware, it should still pass because the override branch in 6b applies the resolved sink last.

---

## Step 7 — Add the misconfiguration-fails-at-startup integration check

The deletion test from the epic ticket says: "Misconfiguration fails at startup with a clear traceback to `PolicyContext.from_settings`, not on the first tool call." Step 1 already added the unit test. Now wire one server-level test that demonstrates `create_mcp` re-raises the `ConfigError` cleanly.

- [ ] Append the following test to `tests/unit/test_server.py` (if the file does not exist, create it; otherwise add at the end). Imports go in the existing import block:

```python
def test_create_mcp_raises_config_error_when_signing_keys_missing(
    tmp_path: Path,
) -> None:
    """create_mcp surfaces PolicyContext.from_settings invariant failures."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    settings = Settings(
        modal_config_path=modal_config,
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_allowed_hosts=("127.0.0.1", "localhost"),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
        modal_mcp_read_only=False,
        modal_mcp_enabled_toolsets=READ_ONLY_TOOLSETS | CHANGE_TOOLSETS,
    )
    object.__setattr__(settings, "modal_mcp_signing_keys", None)

    with pytest.raises(ConfigError, match="signing keys"):
        create_mcp(settings, _skip_security_check=True)
```

Required imports for this test (add if missing):

```python
from pathlib import Path

import pytest
from pydantic import SecretStr

from modal_mcp.config import ConfigError, Settings
from modal_mcp.policy.rules import CHANGE_TOOLSETS, READ_ONLY_TOOLSETS
from modal_mcp.server import create_mcp
```

- [ ] Run only this test to confirm it surfaces the invariant:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/unit/test_server.py -k "raises_config_error_when_signing_keys_missing" -v
```

Expected: pass. The traceback (visible in `-v` failure output for the negative case) terminates inside `PolicyContext.from_settings`.

---

## Step 8 — Drop the redundant signing-key resolution in HTTP approval route (optional)

The `_approval_signing_keys_from_settings` helper in `server.py` (lines 77-101) duplicates `PolicyContext._build_signing_keys`. They both parse `settings.modal_mcp_signing_keys` and raise on missing/malformed input. They are kept separate in this plan because the HTTP route raises `ModalAdapterError`, not `ConfigError`, so unifying them changes the wire-level error code semantics.

- [ ] **Decision:** leave `_approval_signing_keys_from_settings` alone. Add a comment above it documenting the duplication is intentional:

```python
# NOTE: parallels PolicyContext._build_signing_keys but raises
# ModalAdapterError (HTTP 401) instead of ConfigError (startup abort). The
# HTTP route runs at request time and must surface a wire-level auth error,
# not a fatal startup error. Do not merge these two helpers without an
# error-code migration plan.
def _approval_signing_keys_from_settings(...): ...
```

- [ ] (No code action; documentation only.)

---

## Step 9 — Final lint and full test run

- [ ] Run ruff:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run ruff check .
```

Expected: zero issues. Apply `uv run ruff check . --fix` for any trivial issues, then re-check.

- [ ] Run the full test suite:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest -v
```

Expected: all tests pass. Pay particular attention to:
- `tests/unit/test_policy.py` — fixture and middleware construction
- `tests/unit/test_policy_context.py` — new file
- `tests/unit/test_server.py` — new misconfiguration test
- Any test that previously imported `NullAuditSink` from `modal_mcp.policy.engine`

- [ ] Run type-checking if configured:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run mypy src/modal_mcp/policy src/modal_mcp/server.py 2>&1 | tail -10
```

Expected: no new errors. (`mypy` may not be wired into the project; skip silently if the command is not configured.)

---

## Step 10 — Commit

- [ ] Stage and commit:

```bash
cd "$(git rev-parse --show-toplevel)" && git add \
    src/modal_mcp/policy/context.py \
    src/modal_mcp/policy/audit.py \
    src/modal_mcp/policy/engine.py \
    src/modal_mcp/server.py \
    tests/unit/test_policy.py \
    tests/unit/test_policy_context.py \
    tests/unit/test_server.py
```

```bash
cd "$(git rev-parse --show-toplevel)" && git commit -m "$(cat <<'EOF'
refactor(policy): concentrate PolicyMiddleware wiring in PolicyContext

PolicyMiddleware.__init__ shrinks from 8 kwargs (each with a lazy
_*_from_settings() default) to 3 positional arguments (mcp, context,
settings). The new PolicyContext frozen dataclass binds every policy
dependency once at server startup. Misconfiguration (e.g. missing
signing keys when read_only=False) now fails at PolicyContext.from_settings
with a clean traceback, not on the first tool call.

NullAuditSink moves out of engine.py into a new policy/audit.py module
that also publishes the AuditSink Protocol. JSONLAuditSink satisfies it
structurally; tests inject NullAuditSink without imports leaking through
engine.py.

Closes epo-replace-policymiddleware-8-kwarg-qd8k
EOF
)"
```

---

## Self-review checklist

### Spec coverage

| Acceptance criterion | Covered by |
|---|---|
| `PolicyContext` frozen dataclass with 7 fields | Step 3 |
| `PolicyContext.from_settings(settings) -> Self` is the single resolution path | Step 3 |
| `AuditSink` Protocol with `record_decision`/`record_error`/`record_result` | Step 2 |
| `NullAuditSink` moved out of `engine.py` | Step 2 + step 4a |
| `PolicyMiddleware.__init__(mcp, context, settings)` — 3 args, no kwargs | Step 4b |
| `_default_mutation_limiter` and `_signing_keys_from_settings` deleted from `engine.py` | Step 4c |
| `server.py` builds `PolicyContext.from_settings(settings)` once | Step 6 |
| Misconfiguration-fails-at-startup invariant (missing signing keys without read-only) | Step 1 unit test + step 7 integration test |
| `tests/unit/test_policy.py` uses a `PolicyContext` fixture, not per-test kwargs | Step 5 |
| `uv run pytest` passes | Step 9 |
| `uv run ruff check .` passes | Step 9 |

### Placeholder scan

No placeholder strings used. Every code block is copy-paste-ready except the test-rewrite template in 5b, which intentionally shows a substitution shape (each test must thread its own fixture parameter).

### Type consistency

- `PolicyContext` fields match the public attribute types currently inferred on `PolicyMiddleware` (`approval_ledger: ApprovalTokenLedger`, `rate_limiter: TokenBucketRateLimiter`, `mutation_limiter: TokenBucketRateLimiter | None`, `signing_keys: tuple[tuple[str, bytes], ...]`, `now: Callable[[], int]`).
- `AuditSink` is a structural Protocol (`@runtime_checkable`). The existing `JSONLAuditSink` (in `observability/audit.py`) already implements the three required methods with compatible signatures — no changes needed there.
- `actor_resolver` keeps the existing `Callable[[MiddlewareContext[Any]], ApprovalActor]` shape. The forward type alias in `policy/context.py` uses `Callable[[object], ApprovalActor]` to avoid pulling FastMCP types into the dataclass module; this is a covariant widening that does not break callers.
- `from_settings(settings) -> Self` uses PEP 673 `Self` so subclass support is automatic.

### Misconfiguration invariant

The epic's central deletion test — "misconfiguration fails at startup with a clear traceback to `PolicyContext.from_settings`, not on the first tool call" — is verified twice:

1. **Unit level (step 1):** `TestPolicyContextStartupInvariants::test_raises_when_signing_keys_missing_and_not_read_only` calls `PolicyContext.from_settings(...)` directly with a mutated `Settings` and asserts `ConfigError`.
2. **Integration level (step 7):** `test_create_mcp_raises_config_error_when_signing_keys_missing` constructs the full server via `create_mcp` and asserts the same `ConfigError` propagates.

Both tests verify the failure happens **before** any tool call is issued, satisfying the epic's deletion test.

### Blast-radius justification for keeping `_approval_signing_keys_from_settings`

The HTTP approval route raises `ModalAdapterError(UNAUTHORIZED)` to surface a wire-level 401, while `PolicyContext` raises `ConfigError` to abort startup. The two errors map to different user-facing behaviors (HTTP response vs. process exit) and consolidating them requires a separate error-code migration. Step 8 documents the duplication is intentional and out of scope.

### `__all__` audit

`engine.py.__all__` loses `NullAuditSink`. Callers must import from `modal_mcp.policy.audit`. The grep in step 4c catches every internal call site; external callers (none in this monorepo) would receive a clean `ImportError` at module load time, not a runtime surprise.

### Test-fixture ergonomics

`policy_context` is built from `policy_settings` and parameterized with `dataclasses.replace`. Tests that previously passed three or four overlapping kwargs to `PolicyMiddleware(...)` now express their intent in one `replace(...)` call. This is the same complexity-concentration argument the epic makes: one place to read what the middleware sees.
