# Precompute OriginGuard Allowed Sets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `OriginGuard`'s per-request `_normalized_allowed_origins(settings)` / `_normalized_allowed_hosts(settings)` recomputation with `frozenset[str]` attributes precomputed in `OriginGuard.__init__`. Construction now takes explicit `allowed_origins` / `allowed_hosts` sequences (no `Settings`). Bad entries raise `ConfigError` at startup (loud) rather than silently rejecting every request (quiet).

**Architecture:** `OriginGuard.__init__` accepts two keyword-only `Sequence[str]` parameters and pre-normalises each entry via a new static `_build_allowed_set(entries, *, kind)` helper that delegates to the existing `_normalize_origin` / `_normalize_host` parsers. The hot path becomes: two header lookups, two `frozenset` membership checks — no `urllib.parse` per request. `server.create_asgi_app` constructs the middleware via `Middleware(OriginGuard, allowed_origins=settings.modal_mcp_allowed_origins, allowed_hosts=settings.modal_mcp_allowed_hosts)`. The free `validate_origin(origin, host, settings)` function is removed in favour of a small `OriginGuard._reject_reason` instance helper used only by `__call__`; tests that previously called `validate_origin` are split into two groups: construction-time tests (bad config → `ConfigError`) and runtime tests (built guard + headers).

**Tech Stack:** Python 3.12, Starlette ASGI, pydantic-settings, pytest, pytest-asyncio, ruff

---

## File Structure

Files touched by this plan:

```text
src/modal_mcp/asgi.py                       ← OriginGuard refactor; remove validate_origin; raise ConfigError
src/modal_mcp/server.py                     ← pass explicit lists to Middleware(OriginGuard, ...)
tests/integration/test_security.py          ← split validate_origin tests into construction vs runtime
tests/integration/test_http_mcp.py          ← no structural change; OriginGuard.cls assertion still holds
```

No new modules. `ConfigError` is reused from `modal_mcp.config` (already exported); no new error type is introduced.

---

## Step 1 — Write failing tests for the new constructor contract

The new tests cover three responsibilities:

1. **Construction-time validation** — bad entries raise `ConfigError` naming the offending value.
2. **Runtime hot path** — built guard accepts/rejects requests using only precomputed sets.
3. **`Settings` no longer threaded through** — guard does not store the `Settings` instance.

- [ ] Open `tests/integration/test_security.py` and replace the existing block of `validate_origin` / OriginGuard tests (the existing tests starting at `test_validate_origin_rejects_invalid_or_missing_origin` and running through `test_origin_guard_allows_valid_request_to_pass_through`) with the block below. Keep all earlier tests (`test_runtime_security_*`, hosted-mode tests, etc.) untouched.

```python
# --- OriginGuard construction-time validation -------------------------------


@pytest.mark.parametrize(
    ("bad_entry", "kind"),
    [
        ("ftp://mcp.example.com", "origin"),
        ("http://user:pw@mcp.example.com", "origin"),
        ("http://mcp.example.com/path", "origin"),
        ("http://mcp.example.com?x=1", "origin"),
        ("http://mcp.example.com#frag", "origin"),
        ("null", "origin"),
        ("", "origin"),
    ],
)
def test_origin_guard_init_rejects_malformed_origin_entry(
    bad_entry: str,
    kind: str,
) -> None:
    """Malformed allowed-origin entries fail loudly at startup."""

    del kind  # used only for parameter labelling
    with pytest.raises(ConfigError, match=r"MODAL_MCP_ALLOWED_ORIGINS"):
        OriginGuard(
            _noop_app,
            allowed_origins=("http://127.0.0.1:8765", bad_entry),
            allowed_hosts=("127.0.0.1",),
        )


@pytest.mark.parametrize(
    "bad_entry",
    [
        "http://user@host",
        "host:not-a-port",
        "host/path",
        "host?x=1",
        "",
    ],
)
def test_origin_guard_init_rejects_malformed_host_entry(bad_entry: str) -> None:
    """Malformed allowed-host entries fail loudly at startup."""

    with pytest.raises(ConfigError, match=r"MODAL_MCP_ALLOWED_HOSTS"):
        OriginGuard(
            _noop_app,
            allowed_origins=("http://127.0.0.1:8765",),
            allowed_hosts=("127.0.0.1", bad_entry),
        )


def test_origin_guard_init_names_offending_value_in_error() -> None:
    """The ConfigError message includes the bad value (so operators can grep logs)."""

    with pytest.raises(ConfigError, match=r"ftp://bad\.example\.com"):
        OriginGuard(
            _noop_app,
            allowed_origins=("http://127.0.0.1:8765", "ftp://bad.example.com"),
            allowed_hosts=("127.0.0.1",),
        )


def test_origin_guard_init_does_not_store_settings() -> None:
    """The guard must not retain Settings; only precomputed sets are kept."""

    guard = OriginGuard(
        _noop_app,
        allowed_origins=("http://127.0.0.1:8765",),
        allowed_hosts=("127.0.0.1", "localhost"),
    )

    for attr in vars(guard):
        value = getattr(guard, attr)
        assert not isinstance(value, Settings), (
            f"OriginGuard.{attr} unexpectedly holds Settings"
        )


def test_origin_guard_init_precomputes_frozen_sets() -> None:
    """Allowed sets are immutable frozensets, normalised once at construction."""

    guard = OriginGuard(
        _noop_app,
        allowed_origins=("HTTP://127.0.0.1:8765",),
        allowed_hosts=("LocalHost",),
    )

    # Private attributes are part of this module's contract; tests intentionally
    # peek to verify the precomputation invariant.
    allowed_origins = guard._allowed_origins
    allowed_hosts = guard._allowed_hosts
    assert isinstance(allowed_origins, frozenset)
    assert isinstance(allowed_hosts, frozenset)
    assert allowed_origins == frozenset({"http://127.0.0.1:8765"})
    assert allowed_hosts == frozenset({"localhost"})


def test_origin_guard_init_accepts_empty_inputs_as_caller_responsibility() -> None:
    """Empty inputs build an empty allowlist; Settings layer enforces non-empty."""

    guard = OriginGuard(
        _noop_app,
        allowed_origins=(),
        allowed_hosts=(),
    )
    assert guard._allowed_origins == frozenset()
    assert guard._allowed_hosts == frozenset()


# --- OriginGuard runtime hot path -------------------------------------------


def _make_guard(
    *,
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8765",
        "https://mcp.example.com",
    ),
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "mcp.example.com"),
    downstream: Any | None = None,
) -> OriginGuard:
    async def _noop(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive, send

    return OriginGuard(
        downstream or _noop,
        allowed_origins=allowed_origins,
        allowed_hosts=allowed_hosts,
    )


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        ("http://127.0.0.1:8765", "localhost:8765"),
        ("https://mcp.example.com", "mcp.example.com"),
    ],
)
@pytest.mark.asyncio
async def test_origin_guard_accepts_allowlisted_requests(
    origin: str,
    host: str,
) -> None:
    """Allowlisted (origin, host) pairs pass through to the wrapped app."""

    called = False

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guard = _make_guard(downstream=downstream)
    messages = await _invoke(guard, _http_scope(origin, host))

    assert called is True
    assert messages[0]["status"] == 204


@pytest.mark.parametrize(
    "origin",
    [None, "null", "chrome-extension://abcd", "ftp://mcp.example.com"],
)
@pytest.mark.asyncio
async def test_origin_guard_rejects_invalid_or_missing_origin(
    origin: str | None,
) -> None:
    """Missing/null/non-HTTP request origins fail closed with 403."""

    guard = _make_guard()
    messages = await _invoke(guard, _http_scope(origin, "localhost:8765"))

    assert messages[0]["status"] == 403


@pytest.mark.asyncio
async def test_origin_guard_rejects_unlisted_origin() -> None:
    """Origins outside the precomputed set fail closed with 403."""

    guard = _make_guard()
    messages = await _invoke(
        guard,
        _http_scope("https://evil.example.com", "localhost:8765"),
    )

    assert messages[0]["status"] == 403


@pytest.mark.asyncio
async def test_origin_guard_rejects_unlisted_host() -> None:
    """Hosts outside the precomputed set fail closed with 403."""

    guard = _make_guard()
    messages = await _invoke(
        guard,
        _http_scope("https://mcp.example.com", "evil.example.com"),
    )

    assert messages[0]["status"] == 403


@pytest.mark.asyncio
async def test_origin_guard_rejects_missing_host_header() -> None:
    """Missing Host header fails closed instead of trusting the ASGI server bind."""

    called = False

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive, send
        nonlocal called
        called = True

    guard = _make_guard(downstream=downstream)
    scope = _http_scope("http://127.0.0.1:8765", None)
    scope["server"] = ("127.0.0.1", 8765)
    messages = await _invoke(guard, scope)

    assert called is False
    assert messages[0]["status"] == 403


async def _noop_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    del scope, receive, send
```

- [ ] Update the top-of-file imports in `tests/integration/test_security.py` to drop the deleted symbols. Replace the existing `from modal_mcp.asgi import OriginGuard, OriginValidationError, validate_origin` line with:

```python
from modal_mcp.asgi import OriginGuard
```

- [ ] Run the security-tests file to confirm the new tests **fail** (red):

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/integration/test_security.py -x 2>&1 | tail -40
```

Expected: collection errors or `TypeError: OriginGuard.__init__() got an unexpected keyword argument 'allowed_origins'`. This confirms the new contract is not yet implemented.

---

## Step 2 — Implement the new `OriginGuard` constructor and hot path

- [ ] Rewrite `src/modal_mcp/asgi.py` to the contents below. The diff removes `validate_origin`, removes `_normalized_allowed_*`, removes `Settings` import, and adds `_build_allowed_set` + the new keyword-only constructor.

```python
"""ASGI middleware for request-origin validation."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from starlette.status import HTTP_403_FORBIDDEN
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import ConfigError


class OriginValidationError(ValueError):
    """Raised when an incoming request fails origin validation."""


def _decode_header_value(value: bytes) -> str:
    return value.decode("latin-1").strip()


def _get_header(scope: Scope, name: bytes) -> str | None:
    headers = scope.get("headers")
    if not headers:
        return None
    target = name.lower()
    for key, value in headers:
        if key.lower() == target:
            return _decode_header_value(value)
    return None


def _normalize_host(host: str | None) -> str | None:
    if host is None:
        return None
    candidate = host.strip()
    if not candidate:
        return None
    parsed = urlsplit(f"http://{candidate}")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        if parsed.port is not None:
            pass
    except ValueError:
        return None
    return parsed.hostname.lower()


def _format_origin_host(hostname: str) -> str:
    if ":" in hostname:
        return f"[{hostname}]"
    return hostname


def _normalize_origin(origin: str | None) -> str | None:
    if origin is None:
        return None
    candidate = origin.strip()
    if not candidate or candidate.lower() == "null":
        return None
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = _format_origin_host(parsed.hostname.lower())
    try:
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}:
        port = None
    if port is None:
        return f"{parsed.scheme.lower()}://{host}"
    return f"{parsed.scheme.lower()}://{host}:{port}"


_NORMALIZERS = {
    "origin": _normalize_origin,
    "host": _normalize_host,
}
_ENV_VAR = {
    "origin": "MODAL_MCP_ALLOWED_ORIGINS",
    "host": "MODAL_MCP_ALLOWED_HOSTS",
}


class OriginGuard:
    """ASGI middleware that rejects untrusted browser origins before MCP handling.

    Allowed origins and hosts are normalised once at construction time and stored
    as ``frozenset[str]``.  The per-request hot path is two header lookups plus
    two set-membership checks; the URL parser is never invoked per request.

    Malformed allowlist entries raise :class:`ConfigError` at startup, naming the
    bad value, so configuration mistakes surface loudly rather than silently
    rejecting every subsequent request.
    """

    __slots__ = ("_allowed_hosts", "_allowed_origins", "_app")

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: Sequence[str],
        allowed_hosts: Sequence[str],
    ) -> None:
        self._app = app
        self._allowed_origins = self._build_allowed_set(allowed_origins, kind="origin")
        self._allowed_hosts = self._build_allowed_set(allowed_hosts, kind="host")

    @staticmethod
    def _build_allowed_set(
        entries: Sequence[str],
        *,
        kind: str,
    ) -> frozenset[str]:
        """Normalise and validate ``entries`` once; raise :class:`ConfigError` on bad input.

        ``kind`` is ``"origin"`` or ``"host"`` and selects which normaliser and
        which env-var name to mention in the error message.
        """

        normalise = _NORMALIZERS[kind]
        env_var = _ENV_VAR[kind]
        normalised: set[str] = set()
        for raw in entries:
            value = normalise(raw)
            if value is None:
                msg = (
                    f"{env_var} entry is not a valid {kind}: {raw!r}"
                )
                raise ConfigError(msg)
            normalised.add(value)
        return frozenset(normalised)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Validate the request origin before delegating to the wrapped app."""

        if scope["type"] not in {"http", "websocket"}:
            await self._app(scope, receive, send)
            return

        origin = _normalize_origin(_get_header(scope, b"origin"))
        if origin is None or origin not in self._allowed_origins:
            await self._reject(scope, send)
            return

        host = _normalize_host(_get_header(scope, b"host"))
        if host is None or host not in self._allowed_hosts:
            await self._reject(scope, send)
            return

        await self._app(scope, receive, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b"Forbidden"
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": HTTP_403_FORBIDDEN,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = ["OriginGuard", "OriginValidationError"]
```

- [ ] Run the new security tests and confirm they **pass** (green):

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/integration/test_security.py -v 2>&1 | tail -60
```

Expected: all new construction-time and runtime tests pass.

---

## Step 3 — Update `server.py` to pass explicit lists to the middleware

The middleware is currently constructed via `Middleware(OriginGuard, settings=resolved_settings)`. After this step, `Settings` no longer leaks into the ASGI layer.

- [ ] In `src/modal_mcp/server.py`, locate the line inside `create_asgi_app`:

```python
    middleware = [Middleware(OriginGuard, settings=resolved_settings)]
```

  Replace with:

```python
    middleware = [
        Middleware(
            OriginGuard,
            allowed_origins=resolved_settings.modal_mcp_allowed_origins,
            allowed_hosts=resolved_settings.modal_mcp_allowed_hosts,
        )
    ]
```

- [ ] Confirm no other module imports `validate_origin` or `OriginValidationError` (the latter is still exported for backward compatibility but is no longer raised inside the guard):

```bash
cd "$(git rev-parse --show-toplevel)" && colgrep -e "validate_origin|OriginValidationError"
```

Expected: matches only in `src/modal_mcp/asgi.py` (the class definition) and `tests/integration/test_security.py` if a stale import remains — if so, remove it. No production-code call sites of `validate_origin` should remain.

---

## Step 4 — Verify loud-failure path at server startup

`Settings._validate_startup_contract` already requires the env vars to be non-empty (raises `ConfigError` if missing). After this plan, the guard *also* rejects malformed entries when `create_asgi_app` builds the middleware stack. This step adds one focused test that proves the failure surfaces at server startup, not at the first HTTP request.

- [ ] Append the following test to `tests/integration/test_security.py`:

```python
def test_create_asgi_app_fails_loudly_on_malformed_allowed_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad MODAL_MCP_ALLOWED_ORIGINS entry must fail at startup, not per request."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    settings = Settings(
        modal_config_path=modal_config,
        modal_mcp_allowed_origins=(
            "http://127.0.0.1:8765",
            "ftp://bad.example.com",   # malformed: non-http scheme
        ),
        modal_mcp_allowed_hosts=("127.0.0.1",),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
    )

    with pytest.raises(ConfigError, match=r"ftp://bad\.example\.com"):
        create_asgi_app(settings)
```

- [ ] Run the security test file and confirm all tests pass:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/integration/test_security.py -v 2>&1 | tail -40
```

Expected: every test green, including `test_create_asgi_app_fails_loudly_on_malformed_allowed_origin`.

---

## Step 5 — Verify no regressions in HTTP / approval suites

`tests/integration/test_http_mcp.py` contains the structural assertion `assert any(middleware.cls is OriginGuard for middleware in mcp_app.user_middleware)` — this still holds because `Middleware(OriginGuard, ...)` keeps the `cls` attribute set. No test edits are required there.

- [ ] Run the HTTP integration suite to confirm structural and approval flows still pass:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest tests/integration/test_http_mcp.py -v 2>&1 | tail -40
```

Expected: all tests pass; no failure mentioning `OriginGuard`, `settings=`, or `validate_origin`.

- [ ] Confirm nothing else in the repo references the deleted symbols:

```bash
cd "$(git rev-parse --show-toplevel)" && colgrep -e "validate_origin|_normalized_allowed_origins|_normalized_allowed_hosts"
```

Expected: zero matches. If any survive, remove them.

---

## Step 6 — Linting and full test run

- [ ] Run ruff:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run ruff check .
```

  Expected: no errors.

- [ ] Run the full test suite:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest -q
```

  Expected: all tests pass.

- [ ] Commit:

```bash
cd "$(git rev-parse --show-toplevel)" && git add \
    src/modal_mcp/asgi.py \
    src/modal_mcp/server.py \
    tests/integration/test_security.py
git commit -m "refactor(asgi): precompute OriginGuard allowed sets; fail loudly on bad config

Closes epo-precompute-originguard-allowed-s-xe1r"
```

---

## Self-review checklist

### Spec coverage

| Acceptance criterion | Covered by |
|---|---|
| `OriginGuard.__init__(app, *, allowed_origins, allowed_hosts)` (no `Settings`) | Step 2 |
| Allowed sets are `frozenset[str]`, normalised once at construction | Step 2 (`_build_allowed_set`) |
| Bad config raises `ConfigError` naming the offending value | Step 2 (`_build_allowed_set` message) + Step 1 / Step 4 tests |
| Hot path = 2 header lookups + 2 set-membership checks | Step 2 (`__call__` body) |
| `server.create_asgi_app` constructs middleware with explicit lists | Step 3 |
| Failure surfaces at `create_asgi_app(...)` not at first request | Step 4 test |
| Existing HTTP / approval integration tests untouched | Step 5 |
| `uv run pytest` + `uv run ruff check .` pass | Step 6 |

### Backward-compatibility notes

- `validate_origin(origin, host, settings)` and `_normalized_allowed_*` are **removed**, not deprecated. The only known caller was inside `OriginGuard.__call__` itself; production code paths do not call `validate_origin` directly. The test suite is migrated in Step 1.
- `OriginValidationError` is **kept** in `__all__` for downstream importers but is no longer raised internally — the hot path now short-circuits to `_reject` directly. Removing the class entirely is out of scope for this epic.

### Type consistency

- `__init__` declares `allowed_origins: Sequence[str]` and `allowed_hosts: Sequence[str]` (matching the runtime shape produced by `Settings`' `tuple[str, ...]` fields). `_build_allowed_set` returns `frozenset[str]`. `__slots__` is used so the instance footprint is two refs + the wrapped app.
- `ConfigError` (subclass of `ValueError`) is the same error type raised elsewhere by `Settings._validate_startup_contract`, so operators see one consistent failure mode at startup.

### Hot-path invariant

`__call__` performs zero `urllib.parse` work for already-validated request headers when those headers belong to the precomputed allowlist — only the per-request `_normalize_origin` / `_normalize_host` calls remain, and those are unavoidable (they parse the incoming header). The per-request reparsing of *configured* origins/hosts (the previous `_normalized_allowed_*(settings)` work) is gone.

### Failure-mode invariant

Bad config now fails **once at startup** (via `_build_allowed_set` → `ConfigError`) instead of **per request** (via every request returning 403 silently). Operators see the bad value in the startup traceback rather than chasing missing-403 reports from clients.
