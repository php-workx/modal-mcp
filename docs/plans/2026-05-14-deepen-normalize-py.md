# Deepen normalize.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 852-line flat-helper module `domain/normalize.py` with per-entity normalizer classes that each expose `normalize(raw) -> tuple[Entity | None, list[str]]`, hiding signing keys, TTL, sentinel logic, and field-name fallbacks from all callers.

**Architecture:** Add one class per entity to normalize.py alongside the existing functions (additive migration — tests pass at every commit). Wire the new classes into `modal_adapter.py` via constructor injection. Delete `_normalize_safely` and the legacy `normalize_*` public names last. ContainerNormalizer accepts an optional `hint_task_id` per-call because the adapter knows the task ID from the RPC request but not from the response body.

**Tech Stack:** Python 3.12, pydantic v2, pytest, ruff, existing `domain/refs.py` (encode_ref / decode_ref unchanged).

---

## File Structure

Files modified by this plan and what each change does:

| File | Change |
|---|---|
| `src/modal_mcp/domain/normalize.py` | Add 8 normalizer classes; update `__all__` |
| `src/modal_mcp/adapters/modal_adapter.py` | Constructor injection; inline list loops; delete `_normalize_safely` |
| `tests/unit/test_normalize.py` | Add class-based test groups; migrate existing direct-function tests |

No new files created. No other files touched.

### New classes added to normalize.py

```
WorkspaceNormalizer     → wraps normalize_workspace     (required=False, never None)
EnvironmentNormalizer   → wraps normalize_environment   (required=False, never None)
AppNormalizer           → wraps normalize_app           (app_id required → None on failure)
ContainerNormalizer     → wraps normalize_container     (task_id required → None on failure; hint_task_id kwarg)
VolumeNormalizer        → wraps normalize_volume        (name required → None on failure)
SandboxNormalizer       → wraps normalize_sandbox       (sandbox_id required → None on failure)
DeploymentNormalizer    → wraps normalize_deployment    (multiple required fields → None on failure)
LogBatchNormalizer      → wraps normalize_log_batch     (secret_strings at construction)
```

### Constructor signature (all classes except LogBatchNormalizer)

```python
def __init__(
    self,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    ttl: int = 3600,
) -> None:
```

### LogBatchNormalizer constructor

```python
def __init__(
    self,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    ttl: int = 3600,
    secret_strings: Sequence[str] | None = None,
) -> None:
```

### ContainerNormalizer.normalize signature

```python
def normalize(self, raw: Any, *, hint_task_id: str | None = None) -> tuple[Container | None, list[str]]:
```

All other normalizer classes use:
```python
def normalize(self, raw: Any) -> tuple[EntityType | None, list[str]]:
```

---

## Task 1: WorkspaceNormalizer

**Files:**
- Modify: `src/modal_mcp/domain/normalize.py` — add class after `normalize_workspace` function
- Modify: `tests/unit/test_normalize.py` — add `TestWorkspaceNormalizer` class

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_normalize.py` (after existing imports, add `WorkspaceNormalizer` to the import from normalize):

```python
from modal_mcp.domain.normalize import (
    WorkspaceNormalizer,
    # ... existing imports unchanged
)


class TestWorkspaceNormalizer:
    def setup_method(self) -> None:
        self.n = WorkspaceNormalizer(signing_keys=SIGNING_KEYS)

    def test_normalize_full(self) -> None:
        ws, warnings = self.n.normalize({
            "workspace_id": "ws-1",
            "name": "acme",
            "source": "authenticated_token",
            "current": True,
        })
        assert ws is not None
        assert ws.name == "acme"
        assert warnings == []

    def test_normalize_empty_returns_sentinel_not_none(self) -> None:
        # workspace uses required=False so {} never produces None
        ws, warnings = self.n.normalize({})
        assert ws is not None
        assert ws.name == ""
        assert warnings == []

    def test_normalize_returns_existing_model_unchanged(self) -> None:
        existing = Workspace(
            workspace_ref="mref1.ws",
            name="x",
            source="authenticated_token",
            current=False,
        )
        ws, warnings = self.n.normalize(existing)
        assert ws is existing
        assert warnings == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_normalize.py::TestWorkspaceNormalizer -v
```
Expected: `ImportError: cannot import name 'WorkspaceNormalizer'`

- [ ] **Step 3: Implement WorkspaceNormalizer**

Add to `src/modal_mcp/domain/normalize.py`, immediately after the `normalize_workspace` function (around line 337):

```python
class WorkspaceNormalizer:
    """Normalizes workspace-like raw objects. Never returns None (required=False)."""

    def __init__(
        self,
        signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
        ttl: int = 3600,
    ) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[Workspace | None, list[str]]:
        try:
            return (
                normalize_workspace(raw, signing_keys=self._signing_keys, ttl=self._ttl),
                [],
            )
        except Exception as exc:
            return None, [str(exc)]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_normalize.py::TestWorkspaceNormalizer -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/modal_mcp/domain/normalize.py tests/unit/test_normalize.py
git commit -m "feat(normalize): add WorkspaceNormalizer class"
```

---

## Task 2: EnvironmentNormalizer

**Files:**
- Modify: `src/modal_mcp/domain/normalize.py` — add class after `normalize_environment`
- Modify: `tests/unit/test_normalize.py` — add `TestEnvironmentNormalizer`

- [ ] **Step 1: Write the failing test**

Add `EnvironmentNormalizer` to the import in `tests/unit/test_normalize.py`, then add:

```python
class TestEnvironmentNormalizer:
    def setup_method(self) -> None:
        self.n = EnvironmentNormalizer(signing_keys=SIGNING_KEYS)

    def test_normalize_full(self) -> None:
        env, warnings = self.n.normalize({
            "environment_id": "env-1",
            "name": "prod",
            "is_default": True,
        })
        assert env is not None
        assert env.name == "prod"
        assert warnings == []

    def test_normalize_empty_returns_sentinel_not_none(self) -> None:
        env, warnings = self.n.normalize({})
        assert env is not None
        assert env.name == ""
        assert warnings == []

    def test_normalize_returns_existing_model_unchanged(self) -> None:
        existing = Environment(environment_ref="mref1.env", name="prod", is_default=True)
        env, warnings = self.n.normalize(existing)
        assert env is existing
        assert warnings == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_normalize.py::TestEnvironmentNormalizer -v
```
Expected: `ImportError: cannot import name 'EnvironmentNormalizer'`

- [ ] **Step 3: Implement EnvironmentNormalizer**

Add to `src/modal_mcp/domain/normalize.py`, after `normalize_environment` (around line 373):

```python
class EnvironmentNormalizer:
    """Normalizes environment-like raw objects. Never returns None (required=False)."""

    def __init__(
        self,
        signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
        ttl: int = 3600,
    ) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[Environment | None, list[str]]:
        try:
            return (
                normalize_environment(raw, signing_keys=self._signing_keys, ttl=self._ttl),
                [],
            )
        except Exception as exc:
            return None, [str(exc)]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_normalize.py::TestEnvironmentNormalizer -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/modal_mcp/domain/normalize.py tests/unit/test_normalize.py
git commit -m "feat(normalize): add EnvironmentNormalizer class"
```

---

## Task 3: AppNormalizer

**Files:**
- Modify: `src/modal_mcp/domain/normalize.py` — add class after `normalize_app`
- Modify: `tests/unit/test_normalize.py` — add `TestAppNormalizer`

- [ ] **Step 1: Write the failing test**

```python
class TestAppNormalizer:
    def setup_method(self) -> None:
        self.n = AppNormalizer(signing_keys=SIGNING_KEYS)

    def test_normalize_full(self) -> None:
        app, warnings = self.n.normalize({
            "app_id": "ap-1",
            "name": "api",
            "state": "running",
            "n_running_tasks": 2,
            "environment_id": "env-1",
        })
        assert app is not None
        assert app.name == "api"
        assert warnings == []

    def test_normalize_missing_app_id_returns_none_with_warning(self) -> None:
        app, warnings = self.n.normalize({})
        assert app is None
        assert len(warnings) == 1
        assert "app id is required" in warnings[0]

    def test_normalize_missing_created_at_is_ok(self) -> None:
        app, warnings = self.n.normalize({
            "app_id": "ap-1",
            "name": "api",
            "state": "running",
        })
        assert app is not None
        assert app.created_at is None
        assert warnings == []

    def test_normalize_returns_existing_model_unchanged(self) -> None:
        existing = App(
            app_ref="mref1.app",
            name="api",
            state="running",
            n_running_tasks=0,
        )
        app, warnings = self.n.normalize(existing)
        assert app is existing
        assert warnings == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_normalize.py::TestAppNormalizer -v
```
Expected: `ImportError: cannot import name 'AppNormalizer'`

- [ ] **Step 3: Implement AppNormalizer**

Add after `normalize_app` (around line 436):

```python
class AppNormalizer:
    """Normalizes app-like raw objects. Returns None when app_id is absent."""

    def __init__(
        self,
        signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
        ttl: int = 3600,
    ) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[App | None, list[str]]:
        try:
            return (
                normalize_app(raw, signing_keys=self._signing_keys, ttl=self._ttl),
                [],
            )
        except ValueError as exc:
            return None, [str(exc)]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_normalize.py::TestAppNormalizer -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/modal_mcp/domain/normalize.py tests/unit/test_normalize.py
git commit -m "feat(normalize): add AppNormalizer class"
```

---

## Task 4: ContainerNormalizer

**Files:**
- Modify: `src/modal_mcp/domain/normalize.py` — add class after `normalize_container`
- Modify: `tests/unit/test_normalize.py` — add `TestContainerNormalizer`

- [ ] **Step 1: Write the failing test**

```python
class TestContainerNormalizer:
    def setup_method(self) -> None:
        self.n = ContainerNormalizer(signing_keys=SIGNING_KEYS)

    def test_normalize_with_task_id_in_raw(self) -> None:
        c, warnings = self.n.normalize({"task_id": "ta-1", "state": "running"})
        assert c is not None
        assert c.task_id == "ta-1"
        assert warnings == []

    def test_normalize_with_container_id_field(self) -> None:
        c, warnings = self.n.normalize({"container_id": "ta-2"})
        assert c is not None
        assert c.task_id == "ta-2"
        assert warnings == []

    def test_normalize_with_hint_task_id_fallback(self) -> None:
        c, warnings = self.n.normalize({}, hint_task_id="ta-3")
        assert c is not None
        assert c.task_id == "ta-3"
        assert warnings == []

    def test_normalize_no_task_id_no_hint_returns_none_with_warning(self) -> None:
        c, warnings = self.n.normalize({})
        assert c is None
        assert len(warnings) == 1
        assert "task_id" in warnings[0]

    def test_normalize_returns_existing_model_unchanged(self) -> None:
        existing = Container(
            container_ref="mref1.container",
            task_id="ta-1",
            state="running",
        )
        c, warnings = self.n.normalize(existing)
        assert c is existing
        assert warnings == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_normalize.py::TestContainerNormalizer -v
```
Expected: `ImportError: cannot import name 'ContainerNormalizer'`

- [ ] **Step 3: Implement ContainerNormalizer**

Add after `normalize_container` (around line 574):

```python
class ContainerNormalizer:
    """Normalizes container-like raw objects. Returns None when task_id cannot be resolved."""

    def __init__(
        self,
        signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
        ttl: int = 3600,
    ) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(
        self, raw: Any, *, hint_task_id: str | None = None
    ) -> tuple[Container | None, list[str]]:
        try:
            return (
                normalize_container(
                    raw,
                    hint_task_id=hint_task_id,
                    signing_keys=self._signing_keys,
                    ttl=self._ttl,
                ),
                [],
            )
        except ValueError as exc:
            return None, [str(exc)]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_normalize.py::TestContainerNormalizer -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/modal_mcp/domain/normalize.py tests/unit/test_normalize.py
git commit -m "feat(normalize): add ContainerNormalizer class with hint_task_id support"
```

---

## Task 5: VolumeNormalizer and SandboxNormalizer

**Files:**
- Modify: `src/modal_mcp/domain/normalize.py` — add both classes
- Modify: `tests/unit/test_normalize.py` — add both test classes

- [ ] **Step 1: Write the failing tests**

```python
class TestVolumeNormalizer:
    def setup_method(self) -> None:
        self.n = VolumeNormalizer(signing_keys=SIGNING_KEYS)

    def test_normalize_full(self) -> None:
        v, warnings = self.n.normalize({
            "volume_id": "vo-1",
            "name": "data",
            "created_at": "2026-01-01T00:00:00Z",
        })
        assert v is not None
        assert v.name == "data"
        assert warnings == []

    def test_normalize_missing_name_returns_none_with_warning(self) -> None:
        v, warnings = self.n.normalize({"volume_id": "vo-1"})
        assert v is None
        assert len(warnings) == 1
        assert "volume name is required" in warnings[0]

    def test_normalize_missing_created_at_returns_none_with_warning(self) -> None:
        v, warnings = self.n.normalize({"volume_id": "vo-1", "name": "data"})
        assert v is None
        assert len(warnings) == 1
        assert "created_at" in warnings[0]


class TestSandboxNormalizer:
    def setup_method(self) -> None:
        self.n = SandboxNormalizer(signing_keys=SIGNING_KEYS)

    def test_normalize_full(self) -> None:
        s, warnings = self.n.normalize({
            "sandbox_id": "sb-1",
            "created_at": "2026-01-01T00:00:00Z",
            "status": "running",
        })
        assert s is not None
        assert s.sandbox_id == "sb-1"
        assert warnings == []

    def test_normalize_missing_id_returns_none_with_warning(self) -> None:
        s, warnings = self.n.normalize({"created_at": "2026-01-01T00:00:00Z"})
        assert s is None
        assert len(warnings) == 1
        assert "sandbox_id" in warnings[0]

    def test_normalize_missing_created_at_returns_none_with_warning(self) -> None:
        s, warnings = self.n.normalize({"sandbox_id": "sb-1"})
        assert s is None
        assert len(warnings) == 1
        assert "created_at" in warnings[0]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_normalize.py::TestVolumeNormalizer tests/unit/test_normalize.py::TestSandboxNormalizer -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement VolumeNormalizer and SandboxNormalizer**

Add after `normalize_volume` (around line 624):

```python
class VolumeNormalizer:
    """Normalizes volume-like raw objects. Returns None when name or created_at is absent."""

    def __init__(
        self,
        signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
        ttl: int = 3600,
    ) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[VolumeSummary | None, list[str]]:
        try:
            return (
                normalize_volume(raw, signing_keys=self._signing_keys, ttl=self._ttl),
                [],
            )
        except ValueError as exc:
            return None, [str(exc)]
```

Add after `normalize_sandbox` (around line 684):

```python
class SandboxNormalizer:
    """Normalizes sandbox-like raw objects. Returns None when sandbox_id or created_at is absent."""

    def __init__(
        self,
        signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
        ttl: int = 3600,
    ) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[SandboxSummary | None, list[str]]:
        try:
            return (
                normalize_sandbox(raw, signing_keys=self._signing_keys, ttl=self._ttl),
                [],
            )
        except ValueError as exc:
            return None, [str(exc)]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_normalize.py::TestVolumeNormalizer tests/unit/test_normalize.py::TestSandboxNormalizer -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/modal_mcp/domain/normalize.py tests/unit/test_normalize.py
git commit -m "feat(normalize): add VolumeNormalizer and SandboxNormalizer classes"
```

---

## Task 6: DeploymentNormalizer and LogBatchNormalizer

**Files:**
- Modify: `src/modal_mcp/domain/normalize.py`
- Modify: `tests/unit/test_normalize.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestDeploymentNormalizer:
    def setup_method(self) -> None:
        self.n = DeploymentNormalizer(signing_keys=SIGNING_KEYS)

    def test_normalize_full(self) -> None:
        d, warnings = self.n.normalize({
            "version": 3,
            "deployed_at": "2026-01-01T00:00:00Z",
            "client_version": "0.73.0",
            "deployed_by": "alice",
        })
        assert d is not None
        assert d.version == 3
        assert d.deployed_by == "alice"
        assert warnings == []

    def test_normalize_missing_required_fields_returns_none_with_warning(self) -> None:
        d, warnings = self.n.normalize({})
        assert d is None
        assert len(warnings) == 1


class TestLogBatchNormalizer:
    def setup_method(self) -> None:
        self.n = LogBatchNormalizer(signing_keys=SIGNING_KEYS)

    def test_normalize_empty_batch(self) -> None:
        page, warnings = self.n.normalize({"entries": [], "summary": {}})
        assert page is not None
        assert page.entries == []
        assert warnings == []

    def test_normalize_with_entries(self) -> None:
        page, warnings = self.n.normalize({
            "entries": [{"message": "hello", "source": "stdout"}],
        })
        assert page is not None
        assert len(page.entries) == 1
        assert page.entries[0].message == "hello"
        assert warnings == []

    def test_normalize_with_secret_redaction(self) -> None:
        n = LogBatchNormalizer(signing_keys=SIGNING_KEYS, secret_strings=["secret123"])
        page, warnings = n.normalize({
            "entries": [{"message": "token=secret123 here"}],
        })
        assert page is not None
        assert "[REDACTED]" in page.entries[0].message
        assert "secret123" not in page.entries[0].message
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_normalize.py::TestDeploymentNormalizer tests/unit/test_normalize.py::TestLogBatchNormalizer -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement DeploymentNormalizer and LogBatchNormalizer**

Add after `normalize_deployment` (around line 497):

```python
class DeploymentNormalizer:
    """Normalizes deployment history entries. Returns None when required fields are absent."""

    def __init__(
        self,
        signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
        ttl: int = 3600,
    ) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[Deployment | None, list[str]]:
        try:
            return (
                normalize_deployment(raw, signing_keys=self._signing_keys, ttl=self._ttl),
                [],
            )
        except ValueError as exc:
            return None, [str(exc)]
```

Add after `normalize_log_batch` (around line 845):

```python
class LogBatchNormalizer:
    """Normalizes log batch responses with optional secret redaction."""

    def __init__(
        self,
        signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
        ttl: int = 3600,
        secret_strings: Sequence[str] | None = None,
    ) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl
        self._secret_strings = secret_strings

    def normalize(self, raw: Any) -> tuple[LogsPage | None, list[str]]:
        try:
            return (
                normalize_log_batch(
                    raw,
                    signing_keys=self._signing_keys,
                    ttl=self._ttl,
                    secret_strings=self._secret_strings,
                ),
                [],
            )
        except Exception as exc:
            return None, [str(exc)]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_normalize.py::TestDeploymentNormalizer tests/unit/test_normalize.py::TestLogBatchNormalizer -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/modal_mcp/domain/normalize.py tests/unit/test_normalize.py
git commit -m "feat(normalize): add DeploymentNormalizer and LogBatchNormalizer classes"
```

---

## Task 7: Export new classes from `__all__`

**Files:**
- Modify: `src/modal_mcp/domain/normalize.py` — update `__all__`
- Modify: `tests/unit/test_normalize.py` — add export test

- [ ] **Step 1: Write the failing test**

```python
def test_all_normalizer_classes_exported() -> None:
    from modal_mcp.domain import normalize as m
    expected = {
        "AppNormalizer",
        "ContainerNormalizer",
        "DeploymentNormalizer",
        "EnvironmentNormalizer",
        "LogBatchNormalizer",
        "SandboxNormalizer",
        "VolumeNormalizer",
        "WorkspaceNormalizer",
    }
    missing = expected - set(m.__all__)
    assert missing == set(), f"Missing from __all__: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_normalize.py::test_all_normalizer_classes_exported -v
```
Expected: `AssertionError: Missing from __all__: {...}`

- [ ] **Step 3: Update `__all__` in normalize.py**

Replace the existing `__all__` block at the bottom of `src/modal_mcp/domain/normalize.py`:

```python
__all__ = [
    # Normalizer classes — preferred public interface
    "AppNormalizer",
    "ContainerNormalizer",
    "DeploymentNormalizer",
    "EnvironmentNormalizer",
    "LogBatchNormalizer",
    "SandboxNormalizer",
    "VolumeNormalizer",
    "WorkspaceNormalizer",
    # Legacy functions — kept while adapter migration is in-flight; removed in Task 9
    "normalize_app",
    "normalize_container",
    "normalize_deployment",
    "normalize_environment",
    "normalize_log_batch",
    "normalize_sandbox",
    "normalize_volume",
    "normalize_workspace",
]
```

- [ ] **Step 4: Run all normalize tests**

```bash
uv run pytest tests/unit/test_normalize.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/modal_mcp/domain/normalize.py tests/unit/test_normalize.py
git commit -m "feat(normalize): export normalizer classes in __all__"
```

---

## Task 8: Wire normalizer instances into ModalSdkAdapter

Replace all `normalize_X(item, signing_keys=self._signing_keys)` call sites with per-instance normalizer calls. Delete `_normalize_safely`.

**Files:**
- Modify: `src/modal_mcp/adapters/modal_adapter.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_modal_adapter.py`:

```python
def test_adapter_normalize_calls_do_not_pass_signing_keys() -> None:
    """After migration no normalize_* call in the adapter body passes signing_keys."""
    import inspect
    from modal_mcp.adapters.modal_adapter import ModalSdkAdapter
    source = inspect.getsource(ModalSdkAdapter)
    # Find lines that call a normalize_* function with signing_keys=
    offending = [
        line.strip()
        for line in source.splitlines()
        if "normalize_" in line and "signing_keys" in line
        and not line.strip().startswith("#")
    ]
    assert offending == [], f"signing_keys still passed at normalize call sites:\n" + "\n".join(offending)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_modal_adapter.py::test_adapter_normalize_calls_do_not_pass_signing_keys -v
```
Expected: `AssertionError` listing the current signing_keys call sites.

- [ ] **Step 3: Add normalizer imports to modal_adapter.py**

At the top of `src/modal_mcp/adapters/modal_adapter.py`, update the import from normalize to include the new classes (keep legacy functions for now — they are deleted in Task 9):

```python
from modal_mcp.domain.normalize import (
    AppNormalizer,
    ContainerNormalizer,
    DeploymentNormalizer,
    EnvironmentNormalizer,
    LogBatchNormalizer,
    SandboxNormalizer,
    VolumeNormalizer,
    WorkspaceNormalizer,
    normalize_app,
    normalize_container,
    normalize_deployment,
    normalize_environment,
    normalize_log_batch,
    normalize_sandbox,
    normalize_volume,
    normalize_workspace,
)
```

- [ ] **Step 4: Instantiate normalizers in `__init__`**

In `ModalSdkAdapter.__init__`, after the line `self._signing_keys = _parse_signing_keys(settings.modal_mcp_signing_keys)` (around line 130), add:

```python
self._workspace_normalizer = WorkspaceNormalizer(signing_keys=self._signing_keys)
self._environment_normalizer = EnvironmentNormalizer(signing_keys=self._signing_keys)
self._app_normalizer = AppNormalizer(signing_keys=self._signing_keys)
self._container_normalizer = ContainerNormalizer(signing_keys=self._signing_keys)
self._volume_normalizer = VolumeNormalizer(signing_keys=self._signing_keys)
self._sandbox_normalizer = SandboxNormalizer(signing_keys=self._signing_keys)
self._deployment_normalizer = DeploymentNormalizer(signing_keys=self._signing_keys)
self._log_normalizer = LogBatchNormalizer(signing_keys=self._signing_keys)
```

- [ ] **Step 5: Migrate `whoami` (line ~281)**

```python
# Before:
return normalize_workspace(raw, signing_keys=self._signing_keys)

# After:
ws, _ = self._workspace_normalizer.normalize(raw)
# WorkspaceNormalizer never returns None (required=False); _ is always []
return ws  # type: ignore[return-value]
```

Note: add `assert ws is not None` if mypy requires it instead of the type: ignore.

- [ ] **Step 6: Migrate `list_environments` (line ~292)**

```python
# Before:
return self._normalize_safely(
    lambda item: normalize_environment(item, signing_keys=self._signing_keys),
    _items(raw, "items", "environments"),
)

# After:
results: list[Environment] = []
warnings: list[str] = []
for item in _items(raw, "items", "environments"):
    env, item_warnings = self._environment_normalizer.normalize(item)
    warnings.extend(item_warnings)
    if env is not None:
        results.append(env)
return results, warnings
```

- [ ] **Step 7: Migrate `list_apps` (line ~314)**

```python
# Before:
return self._normalize_safely(
    lambda item: normalize_app(item, signing_keys=self._signing_keys),
    _items(raw, "apps", "items"),
)

# After:
results: list[App] = []
warnings: list[str] = []
for item in _items(raw, "apps", "items"):
    app, item_warnings = self._app_normalizer.normalize(item)
    warnings.extend(item_warnings)
    if app is not None:
        results.append(app)
return results, warnings
```

- [ ] **Step 8: Migrate `list_app_deployments` (line ~356)**

```python
# Before:
normalize_deployment(item, signing_keys=self._signing_keys)

# After:
dep, _ = self._deployment_normalizer.normalize(item)
# deployment in a list — skip None items silently (they have no warning surface here)
```

Full replacement for the list comprehension or loop pattern:

```python
results: list[Deployment] = []
for item in _items(raw, "deployments", "items"):
    dep, _ = self._deployment_normalizer.normalize(item)
    if dep is not None:
        results.append(dep)
return results
```

- [ ] **Step 9: Migrate `get_app_logs` / log batch calls (line ~391)**

```python
# Before:
return normalize_log_batch(raw, signing_keys=self._signing_keys)

# After:
page, _ = self._log_normalizer.normalize(raw)
if page is None:
    return LogsPage(
        entries=[],
        summary=LogSummary(
            error_signatures=[],
            top_sources=[],
            total_entries=0,
            truncated=False,
            deduped_count=0,
        ),
    )
return page
```

- [ ] **Step 10: Migrate `list_containers` (line ~435)**

```python
# Before:
return self._normalize_safely(
    lambda item: normalize_container(item, signing_keys=self._signing_keys),
    _items(raw, "tasks", "items"),
)

# After:
results: list[Container] = []
warnings: list[str] = []
for item in _items(raw, "tasks", "items"):
    container, item_warnings = self._container_normalizer.normalize(item)
    warnings.extend(item_warnings)
    if container is not None:
        results.append(container)
return results, warnings
```

- [ ] **Step 11: Migrate `get_container` (line ~440)**

```python
# Before:
return normalize_container(target, hint_task_id=native_task_id, signing_keys=self._signing_keys)

# After:
container, _ = self._container_normalizer.normalize(target, hint_task_id=native_task_id)
return container
```

- [ ] **Step 12: Migrate `list_volumes` (line ~494)**

```python
# Before:
normalize_volume(item, signing_keys=self._signing_keys)

# After (full loop replacement):
results: list[VolumeSummary] = []
for item in _items(raw, "volumes", "items"):
    vol, _ = self._volume_normalizer.normalize(item)
    if vol is not None:
        results.append(vol)
return results
```

- [ ] **Step 13: Migrate `list_sandboxes` and `get_sandbox` (line ~577, ~589)**

For `list_sandboxes`:
```python
results: list[SandboxSummary] = []
for item in _items(raw, "sandboxes", "items"):
    sb, _ = self._sandbox_normalizer.normalize(item)
    if sb is not None:
        results.append(sb)
return results
```

For `get_sandbox`:
```python
# Before:
return normalize_sandbox(raw, signing_keys=self._signing_keys)

# After:
sb, _ = self._sandbox_normalizer.normalize(raw)
return sb
```

- [ ] **Step 14: Delete `_normalize_safely`**

Delete the entire `_normalize_safely` method (lines ~258–270):
```python
# DELETE this entire method:
@staticmethod
def _normalize_safely(
    normalize_fn: Callable[[Any], _T],
    items: list[Any],
) -> tuple[list[_T], list[str]]:
    results: list[_T] = []
    warnings: list[str] = []
    for item in items:
        try:
            results.append(normalize_fn(item))
        except ValueError as exc:
            warnings.append(str(exc))
    return results, warnings
```

Also delete the `_T = TypeVar("_T")` line — it is only used in `_normalize_safely`. Do NOT remove the `Callable` import; it is still needed for `ClientFactory = Callable[[], Any]` at the module level.

- [ ] **Step 15: Run all tests**

```bash
uv run pytest tests/unit/test_modal_adapter.py -v
uv run pytest -q
```
Expected: all pass (673+).

- [ ] **Step 16: Lint**

```bash
uv run ruff check .
uv run ruff format --check src tests
```

- [ ] **Step 17: Commit**

```bash
git add src/modal_mcp/adapters/modal_adapter.py tests/unit/test_modal_adapter.py
git commit -m "refactor(adapter): inject normalizer instances; remove _normalize_safely and per-call signing_keys"
```

---

## Task 9: Remove legacy functions from `__all__`

**Files:**
- Modify: `src/modal_mcp/domain/normalize.py` — remove legacy names from `__all__`
- Modify: `src/modal_mcp/adapters/modal_adapter.py` — remove legacy function imports
- Modify: `tests/unit/test_normalize.py` — migrate direct function calls to class interface

- [ ] **Step 1: Write the failing test**

```python
def test_legacy_normalize_functions_not_in_all() -> None:
    from modal_mcp.domain import normalize as m
    legacy_in_all = [name for name in m.__all__ if name.startswith("normalize_")]
    assert legacy_in_all == [], f"Legacy functions still in __all__: {legacy_in_all}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_normalize.py::test_legacy_normalize_functions_not_in_all -v
```
Expected: `AssertionError` listing the legacy names.

- [ ] **Step 3: Update existing test_normalize.py function-based tests**

The existing tests call `normalize_workspace({...}, signing_keys=SIGNING_KEYS)` directly. Migrate each to use the class interface. For example:

```python
# Before:
workspace = normalize_workspace(
    {"workspace_id": "ws-1", "name": "acme", ...},
    signing_keys=SIGNING_KEYS,
    now=NOW,
)

# After:
n = WorkspaceNormalizer(signing_keys=SIGNING_KEYS)
workspace, _ = n.normalize({"workspace_id": "ws-1", "name": "acme", ...})
assert workspace is not None
```

Do the same for all existing direct calls to `normalize_environment`, `normalize_app`, `normalize_container`, `normalize_volume`, `normalize_sandbox`, `normalize_deployment`, `normalize_log_batch` in the test file.

The `_decode_ref` helper at the top of the test file remains unchanged — it still calls `decode_ref` from `domain.refs` directly.

- [ ] **Step 4: Remove legacy names from `__all__` in normalize.py**

Update `__all__` to contain only the 8 normalizer class names:

```python
__all__ = [
    "AppNormalizer",
    "ContainerNormalizer",
    "DeploymentNormalizer",
    "EnvironmentNormalizer",
    "LogBatchNormalizer",
    "SandboxNormalizer",
    "VolumeNormalizer",
    "WorkspaceNormalizer",
]
```

The `normalize_*` functions themselves remain in the file as private helpers (they are still called by the normalizer classes). They are simply no longer public exports.

- [ ] **Step 5: Remove legacy imports from modal_adapter.py**

In `src/modal_mcp/adapters/modal_adapter.py`, remove the legacy function imports:

```python
# Remove these lines:
normalize_app,
normalize_container,
normalize_deployment,
normalize_environment,
normalize_log_batch,
normalize_sandbox,
normalize_volume,
normalize_workspace,
```

Keep only the class imports.

- [ ] **Step 6: Run all tests**

```bash
uv run pytest -q
```
Expected: all pass.

- [ ] **Step 7: Lint and type check**

```bash
uv run ruff check .
uv run mypy --strict src
```

- [ ] **Step 8: Commit**

```bash
git add src/modal_mcp/domain/normalize.py src/modal_mcp/adapters/modal_adapter.py tests/unit/test_normalize.py
git commit -m "refactor(normalize): remove legacy normalize_* functions from public __all__"
```

---

## Task 10: Final validation

- [ ] **Step 1: Full test suite**

```bash
uv run pytest -q
```
Expected: 673+ passed, 0 failed.

- [ ] **Step 2: Verify no signing_keys at normalize call sites**

```bash
grep -n "signing_keys" src/modal_mcp/adapters/modal_adapter.py
```
Expected: only appears in `__init__` where `self._signing_keys` is set and where normalizers are constructed. No normalize call sites.

- [ ] **Step 3: Verify `_normalize_safely` is gone**

```bash
grep -rn "_normalize_safely" src/
```
Expected: no output.

- [ ] **Step 4: Verify new classes are the only public exports**

```bash
python3 -c "from modal_mcp.domain.normalize import __all__; print(__all__)"
```
Expected output:
```
['AppNormalizer', 'ContainerNormalizer', 'DeploymentNormalizer', 'EnvironmentNormalizer', 'LogBatchNormalizer', 'SandboxNormalizer', 'VolumeNormalizer', 'WorkspaceNormalizer']
```

- [ ] **Step 5: Final commit if any cleanup remains**

```bash
git status
# If any unstaged changes remain:
git add <files>
git commit -m "chore(normalize): final cleanup after normalizer class migration"
```

---

## Acceptance Criteria Checklist

- [ ] 8 normalizer classes exist: `WorkspaceNormalizer`, `EnvironmentNormalizer`, `AppNormalizer`, `ContainerNormalizer`, `VolumeNormalizer`, `SandboxNormalizer`, `DeploymentNormalizer`, `LogBatchNormalizer`
- [ ] Each class: `normalize(raw) -> tuple[Entity | None, list[str]]`
- [ ] `ContainerNormalizer.normalize` accepts optional `hint_task_id` keyword arg
- [ ] `modal_adapter.py` no longer passes `signing_keys` in any `normalize_*` call
- [ ] `_normalize_safely` deleted from `modal_adapter.py`
- [ ] Legacy `normalize_*` names removed from `__all__` (functions still present as internal helpers)
- [ ] `uv run pytest` passes
- [ ] `uv run ruff check .` passes
