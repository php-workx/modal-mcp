# Extract ModalRpcClient Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract `ModalRpcClient` from `ModalSdkAdapter` so that RPC transport (client lifecycle, reconnect retry, proto message construction) is owned by a dedicated class, while the adapter retains only normalization and ref-handling concerns.

**Architecture:** `ModalRpcClient` holds `_client`, `_client_factory`, and the three transport methods (`_call_rpc`, `_call_with_reconnect` renamed to `call`, and `_request` renamed to `request`); `ModalSdkAdapter.__init__` accepts a `ModalRpcClient` instance stored as `self._rpc` and calls through it; `ModalSdkAdapter.create()` constructs the `ModalRpcClient` before building the adapter. The `ModalAdapter` Protocol in `base.py` is untouched.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio, ruff

---

## File Structure

| File | Change |
|---|---|
| `src/modal_mcp/adapters/modal_adapter.py` | Add `ModalRpcClient` class; refactor `ModalSdkAdapter.__init__`, `create`, `aclose`, and every `_call_with_reconnect`/`_request` call site |
| `tests/unit/test_modal_adapter.py` | Update `test_call_with_reconnect_retries_once` and `test_create_uses_injected_client_and_aclose` to reflect that `client_factory` is still accepted by `ModalSdkAdapter.create()` (no structural change needed for most tests; add one focused `ModalRpcClient` unit test) |
| `src/modal_mcp/adapters/base.py` | No change |

---

## Tasks

### Phase 1 — Write a failing test for `ModalRpcClient.call` reconnect

- [ ] **1.1** Open `tests/unit/test_modal_adapter.py` and add the following test directly after the existing `test_call_with_reconnect_retries_once` test (around line 254):

```python
@pytest.mark.asyncio
async def test_modal_rpc_client_call_retries_once(modal_config_path: Path) -> None:
    """ModalRpcClient.call reconnects via factory and retries exactly once."""
    from modal_mcp.adapters.modal_adapter import ModalRpcClient

    first_stub = FakeStub()
    first_stub.fail_once = True
    second_stub = FakeStub()
    first_client = FakeClient(first_stub)
    second_client = FakeClient(second_stub)

    rpc = ModalRpcClient(first_client, client_factory=lambda: second_client)

    # ModalRpcClient.call takes (method_name, request); _request lives on rpc too
    request = rpc.request("Empty")
    result = rpc.call("WorkspaceNameLookup", request)

    assert result["workspace_name"] == "acme"
    assert len(first_stub.requests) == 1   # one attempt before transient failure
    assert len(second_stub.requests) == 1  # one retry on new client
```

- [ ] **1.2** Run the test and confirm it fails with `ImportError` (class does not exist yet):

```bash
cd /Users/runger/workspaces/modal-mcp && uv run pytest tests/unit/test_modal_adapter.py::test_modal_rpc_client_call_retries_once -x 2>&1 | tail -20
```

Expected: `ImportError: cannot import name 'ModalRpcClient'`

---

### Phase 2 — Add `ModalRpcClient` class to `modal_adapter.py`

- [ ] **2.1** Open `src/modal_mcp/adapters/modal_adapter.py`. Immediately above the `class ModalSdkAdapter:` line (line 117), insert the new `ModalRpcClient` class:

```python
class ModalRpcClient:
    """Owns transport: client lifecycle, reconnect, and proto request construction.

    This class is intentionally narrow: it knows nothing about normalizers,
    environment names, or ref decoding. Those concerns live in ModalSdkAdapter.
    """

    def __init__(
        self,
        client: Any,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._client = client
        self._client_factory = client_factory

    # ------------------------------------------------------------------
    # Public transport interface
    # ------------------------------------------------------------------

    def call(self, method_name: str, request: Any | None = None) -> Any:
        """Call a Modal RPC, reconnecting once for transient channel failures."""
        try:
            return self._call_rpc(method_name, request)
        except Exception as exc:
            if not _is_transient_error(exc):
                raise
            if self._client_factory is None:
                msg = f"Modal RPC {method_name} failed with a transient error"
                raise ModalAdapterError(
                    ErrorCode.UPSTREAM_ERROR,
                    msg,
                    retryable=True,
                ) from exc
            self._client = _maybe_await(self._client_factory())
            try:
                return self._call_rpc(method_name, request)
            except Exception as retry_exc:
                msg = f"Modal RPC {method_name} failed after reconnect"
                raise ModalAdapterError(
                    ErrorCode.UPSTREAM_ERROR,
                    msg,
                    retryable=True,
                ) from retry_exc

    def request(self, request_type: str, **fields: Any) -> Any:
        """Build a proto (or plain dict) request message."""
        payload = {key: value for key, value in fields.items() if value is not None}
        if request_type == "Empty":
            return _empty_request()
        try:
            from modal_proto import api_pb2
        except ImportError:
            return payload
        request_cls = getattr(api_pb2, request_type, None)
        if request_cls is None:
            return payload
        try:
            return request_cls(**payload)
        except ValueError:
            return payload

    async def aclose(self) -> None:
        """Close the underlying Modal client if it exposes a close hook."""
        public_close = getattr(self._client, "aclose", None)
        if public_close is not None:
            result = public_close()
            if inspect.isawaitable(result):
                await result
            return

        private_close = getattr(self._client, "_close", None)
        if private_close is None:
            return

        private_close_aio = getattr(private_close, "aio", None)
        if private_close_aio is not None:
            await private_close_aio()
            return

        result = private_close()
        if inspect.isawaitable(result):
            await result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _stub(self) -> Any:
        return getattr(self._client, "stub", self._client)

    def _call_rpc(self, method_name: str, request: Any | None = None) -> Any:
        method = getattr(self._stub, method_name)
        if request is None:
            return method()
        return method(request)
```

- [ ] **2.2** Update `__all__` at the bottom of `modal_adapter.py` to export the new class:

```python
__all__ = ["ModalRpcClient", "ModalSdkAdapter"]
```

---

### Phase 3 — Refactor `ModalSdkAdapter` to delegate to `ModalRpcClient`

- [ ] **3.1** Replace `ModalSdkAdapter.__init__` so it accepts a `ModalRpcClient` and stores it as `self._rpc`. Remove `_client`, `_client_factory` storage. Keep `_settings` and `_signing_keys`:

```python
class ModalSdkAdapter:
    """Read-only Modal adapter backed by an injected or real Modal client.

    Transport concerns (reconnect, client lifecycle, proto construction) are
    delegated entirely to the injected ModalRpcClient.
    """

    def __init__(
        self,
        settings: Settings,
        rpc: ModalRpcClient,
    ) -> None:
        self._settings = settings
        self._rpc = rpc
        self._signing_keys = _parse_signing_keys(settings.modal_mcp_signing_keys)
```

- [ ] **3.2** Replace `ModalSdkAdapter.create()` to construct `ModalRpcClient` before building the adapter. The signature stays the same so callers (including tests) that pass `client=` or `client_factory=` continue to work unchanged:

```python
@classmethod
async def create(
    cls,
    settings: Settings,
    *,
    client: Any | None = None,
    client_factory: ClientFactory | None = None,
) -> ModalSdkAdapter:
    """Create an adapter from injected fakes or the Modal SDK client."""
    if client is None:
        if client_factory is not None:
            client = _maybe_await(client_factory())
        else:
            client = await cls._create_modal_client(settings)
    rpc = ModalRpcClient(client, client_factory=client_factory)
    return cls(settings, rpc)
```

- [ ] **3.3** Replace `ModalSdkAdapter.aclose()` to delegate to `self._rpc.aclose()`:

```python
async def aclose(self) -> None:
    """Close the underlying Modal client via the RPC transport layer."""
    await self._rpc.aclose()
```

- [ ] **3.4** Replace `ModalSdkAdapter._stub` property — remove it entirely (it is now on `ModalRpcClient`).

- [ ] **3.5** Replace `ModalSdkAdapter._request()` — remove it entirely (it is now `ModalRpcClient.request()`).

- [ ] **3.6** Replace `ModalSdkAdapter._call_rpc()` — remove it entirely (it is now on `ModalRpcClient`).

- [ ] **3.7** Replace `ModalSdkAdapter._call_with_reconnect()` — remove it entirely (it is now `ModalRpcClient.call()`).

- [ ] **3.8** Update every call site in `ModalSdkAdapter` that calls `self._call_with_reconnect(...)` to call `self._rpc.call(...)`, and every call to `self._request(...)` to call `self._rpc.request(...)`. There are 14 call sites total (methods that delegate to other methods — `get_environment`, `get_app`, `stat_volume_path`, `get_container_logs` — are excluded because they never call `_call_with_reconnect` directly). Apply the following mechanical substitutions:

  In `validate_auth`:

  ```python
  self._rpc.call("WorkspaceNameLookup", self._rpc.request("Empty"))
  ```

  In `whoami`:

  ```python
  raw = self._rpc.call("WorkspaceNameLookup", self._rpc.request("Empty"))
  ```

  In `list_environments`:

  ```python
  raw = self._rpc.call("EnvironmentList", self._rpc.request("Empty"))
  ```

  In `list_apps`:

  ```python
  request = self._rpc.request("AppListRequest", environment_name=env)
  raw = self._rpc.call("AppList", request)
  ```

  In `list_app_deployments`:

  ```python
  request = self._rpc.request("AppDeploymentHistoryRequest", app_id=native_id)
  raw = self._rpc.call("AppDeploymentHistory", request)
  ```

  In `get_app_logs`:

  ```python
  request = self._rpc.request(
      "AppFetchLogsRequest",
      app_id=native_id,
      since=since,
      until=until,
      limit=limit,
      source=source,
      function_id=function_id,
      function_call_id=function_call_id,
      task_id=task_id,
      sandbox_id=sandbox_id,
      search_text=search_text,
  )
  raw = self._rpc.call("AppFetchLogs", request)
  ```

  In `list_containers`:

  ```python
  request = self._rpc.request(
      "TaskListRequest",
      environment_name=env,
      app_id=native_app_id,
  )
  raw = self._rpc.call("TaskList", request)
  ```

  In `get_container`:

  ```python
  request = self._rpc.request("TaskGetInfoRequest", task_id=native_task_id)
  raw = self._rpc.call("TaskGetInfo", request)
  ```

  In `list_volumes`:

  ```python
  request = self._rpc.request("VolumeListRequest", environment_name=env)
  raw = self._rpc.call("VolumeList", request)
  ```

  In `ls_volume`:

  ```python
  request = self._rpc.request(
      "VolumeListFiles2Request",
      volume_id=self._native_id(volume_id),
      path=path,
      recursive=recursive,
      max_entries=max_entries,
  )
  raw = self._rpc.call("VolumeListFiles2", request)
  ```

  In `read_volume_text`:

  ```python
  request = self._rpc.request(
      "VolumeGetFile2Request",
      volume_id=self._native_id(volume_id),
      path=path,
  )
  raw = self._rpc.call("VolumeGetFile2", request)
  ```

  In `list_sandboxes`:

  ```python
  request = self._rpc.request(
      "SandboxListRequest",
      environment_name=env,
      app_id=self._native_id(app_id, expected_env=env) if app_id else None,
      tags=dict(tags or {}),
      include_finished=include_finished,
  )
  raw = self._rpc.call("SandboxList", request)
  ```

  In `get_sandbox`:

  ```python
  request = self._rpc.request(
      "SandboxWaitRequest",
      sandbox_id=self._native_id(sandbox_id),
  )
  raw = self._rpc.call("SandboxWait", request)
  ```

  In `get_sandbox_stdio`:

  ```python
  request = self._rpc.request(
      "SandboxGetLogsRequest",
      sandbox_id=self._native_id(sandbox_id),
  )
  raw = self._rpc.call("SandboxGetLogs", request)
  ```

---

### Phase 4 — Verify the new `ModalRpcClient` test passes

- [ ] **4.1** Run the new isolated test:

```bash
cd /Users/runger/workspaces/modal-mcp && uv run pytest tests/unit/test_modal_adapter.py::test_modal_rpc_client_call_retries_once -x -v 2>&1 | tail -20
```

Expected: `PASSED`

---

### Phase 5 — Verify all existing adapter tests still pass unchanged

- [ ] **5.1** Run the full adapter test module:

```bash
cd /Users/runger/workspaces/modal-mcp && uv run pytest tests/unit/test_modal_adapter.py -v 2>&1 | tail -30
```

Expected: all tests `PASSED`, no failures.

Key tests to confirm pass without any source change to the test file:

- `test_create_uses_injected_client_and_aclose` — client injection path still works via `create(client=...)`
- `test_call_with_reconnect_retries_once` — factory reconnect still works via `create(client_factory=...)`
- `test_call_with_reconnect_raises_retryable_without_factory` — retryable error still surfaces
- `test_aclose_prefers_modal_private_close_aio` — async close hook still invoked

---

### Phase 6 — Lint check

- [ ] **6.1** Run ruff to confirm zero new warnings:

```bash
cd /Users/runger/workspaces/modal-mcp && uv run ruff check src/modal_mcp/adapters/modal_adapter.py tests/unit/test_modal_adapter.py 2>&1
```

Expected: clean exit (no output).

---

### Phase 7 — Full test suite

- [ ] **7.1** Run the complete test suite:

```bash
cd /Users/runger/workspaces/modal-mcp && uv run pytest 2>&1 | tail -20
```

Expected: all tests pass.

---

### Phase 8 — Commit

- [ ] **8.1** Stage and commit the two changed files:

```bash
cd /Users/runger/workspaces/modal-mcp && git add src/modal_mcp/adapters/modal_adapter.py tests/unit/test_modal_adapter.py && git commit -m "$(cat <<'EOF'
refactor(adapter): extract ModalRpcClient to own transport/reconnect

Separates three concerns previously conflated in ModalSdkAdapter:
- ModalRpcClient owns client lifecycle, reconnect retry, and proto
  message construction (call, request, aclose, _call_rpc, _stub).
- ModalSdkAdapter owns normalization dispatch and ref/env handling;
  delegates all transport to self._rpc (a ModalRpcClient).
- ModalSdkAdapter.create() constructs ModalRpcClient before the
  adapter, preserving the existing client= / client_factory= API
  so all tests pass without changes to call sites.

Closes epo-extract-modalrpcclient-separate--x0e1

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**

- `ModalRpcClient` class with `call(method, request)` and `aclose()` — covered in Phase 2.
- `ModalSdkAdapter.create()` wires `ModalRpcClient`; adapter stores `rpc`, not raw client — covered in Phase 3.2.
- `test_call_with_reconnect_retries_once` passes (existing test unchanged, logic now in `ModalRpcClient.call`) — covered in Phase 5.
- `client_factory` injection via `ModalSdkAdapter.create()` — preserved in Phase 3.2; `ModalRpcClient.__init__` also accepts it directly (Phase 2.1, tested in Phase 1.1).
- All other adapter tests pass unchanged — covered in Phase 5.
- `ModalAdapter` Protocol in `base.py` unchanged — no task touches `base.py`.
- `uv run pytest` passes — Phase 7. `uv run ruff check` passes — Phase 6.

**Placeholder scan:** No `...`, `TODO`, `pass`, or `# implement` stubs in any code block above. All method bodies are complete.

**Type consistency:** `ModalRpcClient.__init__` uses `Any` for `client` (consistent with current adapter), `ClientFactory | None` for `client_factory`. `call()` returns `Any`. `request()` returns `Any`. `aclose()` returns `None`. All match the types used in the current `ModalSdkAdapter`.

**Removed methods from `ModalSdkAdapter`:** `_stub` (property), `_request`, `_call_rpc`, `_call_with_reconnect` — all four are now on `ModalRpcClient` under their new public names. No test directly calls `adapter._call_with_reconnect` or `adapter._request` (tests call public API methods like `whoami`, `list_apps`), so no test updates are required beyond adding the new focused `ModalRpcClient` test.
