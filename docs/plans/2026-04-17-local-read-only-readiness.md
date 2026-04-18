# Local Read-Only Use Readiness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `modal-mcp` reliable enough for a developer to run locally and use read-only Modal MCP tools against a restricted Modal environment.

**Architecture:** Keep v1 self-hosted and read-only. The existing FastMCP server, policy middleware, Modal SDK adapter, and read-only toolsets stay intact; this plan fixes the launch path, adds executable smoke coverage, tightens the docs, and adds a focused validation target for local readiness regressions.

**Tech Stack:** Python 3.12, FastMCP, Starlette, Uvicorn, Modal Python SDK, Pydantic settings, pytest, httpx ASGI transport, `just`.

---

## Non-Goals

- Do not implement hosted OAuth or `/session/create`.
- Do not implement request/session-scoped adapter resolution.
- Do not enable mutating `change` or `expert` tools.
- Do not implement approval UX or mutation execution.
- Do not add Helm, Kubernetes, or remote deployment packaging.
- Do not replace the process-wide adapter for local self-hosted mode.

## Readiness Definition

Local read-only use is ready when all of these are true:

- `uv run modal-mcp` delegates to `modal_mcp.server.run()` instead of exiting immediately.
- `modal_mcp.server.run()` is unit-tested with mocked `uvicorn.run`, so the configured bind host/port and ASGI app handoff are verified without opening sockets.
- The console script remains pointed at `modal_mcp.__main__:main`.
- The server can be smoke-tested without real Modal credentials by injecting a fake adapter in tests.
- The FastMCP Streamable HTTP smoke test enters ASGI lifespan before calling `/mcp`; this is required for FastMCP's session manager task group.
- The documented quickstart starts a local server at `http://127.0.0.1:8765/mcp`.
- The docs distinguish an existing local Modal config from explicit file-backed service-user token setup.
- Default `tools/list` exposes only read-only tools.
- Mutating and expert tools stay hidden/blocked by default.
- Local docs explain credential scope, optional local bearer-token protection, required env vars, and opt-in live Modal verification.
- `just read-only-smoke` exists as a fast targeted diagnostic target.
- `just pre-push` covers the readiness tests through the normal full test suite; the targeted smoke target is not duplicated in `pre-push` unless the full test suite is later narrowed.

## Task 1: Make the CLI Entrypoint Start the Server

**Files:**
- Modify: `src/modal_mcp/__main__.py`
- Test: `tests/unit/test_cli_entrypoint.py`
- Test: `tests/unit/test_server_run.py`

**Step 1: Write the failing CLI delegation test**

Create `tests/unit/test_cli_entrypoint.py`:

```python
"""Tests for the modal-mcp console entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import modal_mcp.__main__ as cli


def test_main_delegates_to_server_run(monkeypatch):
    """The console script should start the server, not parse and exit."""

    called = {}

    def fake_run():
        called["run"] = True

    monkeypatch.setattr(cli.server, "run", fake_run)

    assert cli.main([]) == 0
    assert called == {"run": True}


def test_pyproject_console_script_points_to_cli_main():
    """Packaging metadata should keep modal-mcp pointed at the CLI main."""

    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'modal-mcp = "modal_mcp.__main__:main"' in pyproject
```

**Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/unit/test_cli_entrypoint.py -q
```

Expected: FAIL because `modal_mcp.__main__` does not import `server`, or because `main()` does not call `server.run()`.

**Step 3: Implement the minimal CLI fix**

Update `src/modal_mcp/__main__.py`:

```python
"""CLI entrypoint for modal_mcp."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from modal_mcp import server


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="modal-mcp",
        description="Run the Modal MCP server.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Modal MCP server."""
    build_parser().parse_args(argv)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 4: Add a socket-free `server.run()` test**

Create `tests/unit/test_server_run.py`:

```python
"""Tests for the uvicorn startup wrapper."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import modal_mcp.server as server
from modal_mcp.config import Settings


def test_run_uses_configured_bind_and_asgi_app(monkeypatch, tmp_path):
    """server.run should construct the app and hand it to uvicorn."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    settings = Settings(
        modal_config_path=modal_config,
        modal_mcp_http_bind="127.0.0.1:9876",
        modal_mcp_allowed_origins=("http://127.0.0.1:9876",),
        modal_mcp_allowed_hosts=("127.0.0.1", "localhost"),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
    )

    app = object()
    captured = {}

    monkeypatch.setattr(server, "create_asgi_app", lambda resolved: app)

    def fake_uvicorn_run(app_arg, *, host, port):
        captured.update({"app": app_arg, "host": host, "port": port})

    monkeypatch.setattr(server.uvicorn, "run", fake_uvicorn_run)

    server.run(settings)

    assert captured == {"app": app, "host": "127.0.0.1", "port": 9876}
```

This verifies the runtime wrapper without real Modal credentials, sockets, or a live Uvicorn process.

**Step 5: Run the focused tests**

Run:

```bash
uv run pytest tests/unit/test_cli_entrypoint.py tests/unit/test_server_run.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/modal_mcp/__main__.py tests/unit/test_cli_entrypoint.py tests/unit/test_server_run.py
git commit -s -m "fix: start server from cli entrypoint"
```

## Task 2: Add a Lifespan-Aware MCP HTTP Smoke Test

**Files:**
- Modify: `tests/integration/test_http_mcp.py`

**Step 1: Write the local initialize smoke test**

Add a test near the existing ASGI composition tests:

```python
@pytest.mark.asyncio
async def test_local_asgi_app_serves_mcp_initialize(settings: Settings) -> None:
    """A local self-hosted app should accept MCP initialize over /mcp."""

    async def adapter_factory(_: Settings) -> FakeAdapter:
        return FakeAdapter()

    app = create_asgi_app(settings, adapter_factory=adapter_factory)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8765",
        ) as client:
            response = await client.post(
                "/mcp",
                headers={
                    "Host": "localhost:8765",
                    "Origin": "http://127.0.0.1:8765",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "local-smoke", "version": "0.0.0"},
                    },
                },
            )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "modal-mcp" in response.text
```

The `app.router.lifespan_context(app)` block is required. Without it, FastMCP Streamable HTTP raises `Task group is not initialized` because its session manager has not started.

**Step 2: Run the focused smoke test**

Run:

```bash
uv run pytest tests/integration/test_http_mcp.py::test_local_asgi_app_serves_mcp_initialize -q
```

Expected: PASS. If it fails due to a FastMCP protocol change, update the test to use FastMCP's supported client/session helper rather than weakening production code or bypassing lifespan.

**Step 3: Keep read-only tool visibility explicit**

The existing `test_tools_list_exposes_read_only_tools_only` already asserts the default read-only tool surface. Keep it and strengthen it only if new read-only tools are added.

It must continue to assert:

```python
assert "modal_list_apps" in names
assert "modal_stop_app" not in names
assert "modal_expert_execute" not in names
assert all(tool.annotations.readOnlyHint is True for tool in tools)
```

**Step 4: Run the focused integration tests**

Run:

```bash
uv run pytest tests/integration/test_http_mcp.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/integration/test_http_mcp.py
git commit -s -m "test: smoke local read-only mcp serving"
```

## Task 3: Add a Local Read-Only Smoke Target

**Files:**
- Modify: `Justfile`
- Test: existing test suite and `just` target execution

**Step 1: Add a focused readiness target**

Add this target to `Justfile`:

```just
# Fast targeted check for local read-only startup and tool visibility.
read-only-smoke:
    uv run pytest -q \
      tests/unit/test_cli_entrypoint.py \
      tests/unit/test_server_run.py \
      tests/integration/test_http_mcp.py::test_local_asgi_app_serves_mcp_initialize \
      tests/integration/test_http_mcp.py::test_tools_list_exposes_read_only_tools_only
```

**Step 2: Do not duplicate `pre-push` work**

Current `pre-push` already runs `test`, and `test` runs all non-live tests. Once Task 1 and Task 2 land, `pre-push` already covers the same readiness tests through the full suite.

Keep `read-only-smoke` separate for fast diagnosis. Only add it to `pre-push` if a later change narrows `test` so these checks are no longer included.

**Step 3: Run the targeted target**

Run:

```bash
just read-only-smoke
```

Expected: PASS.

**Step 4: Run the broader gate**

Run:

```bash
just pre-push
```

Expected: PASS. Confirm the output includes the full test suite, not only the smoke target.

**Step 5: Commit**

```bash
git add Justfile
git commit -s -m "chore: add local read-only smoke gate"
```

## Task 4: Make Quickstart Commands Executable and Accurate

**Files:**
- Modify: `README.md`
- Modify: `docs/self-hosting.md`

**Step 1: Split the quickstart into two credential paths**

In `README.md`, make the local quickstart explicit for both supported credential sources.

Path A uses an existing local Modal config:

````markdown
## Quickstart

If you already have a local Modal config at `~/.modal.toml`:

```bash
uv sync --extra dev
export MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765
export MODAL_MCP_ALLOWED_HOSTS=127.0.0.1,localhost
export MODAL_MCP_SIGNING_KEYS=kid1:$(openssl rand -hex 32)
uv run modal-mcp
```

The server listens on `http://127.0.0.1:8765/mcp` by default.
````

Path B uses explicit file-backed service-user credentials:

````markdown
For a cleaner local setup, use a Modal service-user token with Viewer
permissions in a restricted, non-production Modal environment:

```bash
mkdir -p .secrets
printf '%s' '<modal-token-id>' > .secrets/modal-token-id
printf '%s' '<modal-token-secret>' > .secrets/modal-token-secret
printf 'kid1:%s' "$(openssl rand -hex 32)" > .secrets/modal-mcp-signing-key
chmod 600 .secrets/*

export MODAL_TOKEN_ID_FILE=$PWD/.secrets/modal-token-id
export MODAL_TOKEN_SECRET_FILE=$PWD/.secrets/modal-token-secret
export MODAL_MCP_SIGNING_KEY_FILE=$PWD/.secrets/modal-mcp-signing-key
export MODAL_MCP_ALLOWED_ORIGINS=http://127.0.0.1:8765
export MODAL_MCP_ALLOWED_HOSTS=127.0.0.1,localhost
export MODAL_ENVIRONMENT=dev
uv run modal-mcp
```
````

Do not document `install -m 600 /dev/null` for required token files because it creates empty secrets that startup correctly rejects.

**Step 2: Document optional local bearer-token protection**

Add this near the self-hosted auth documentation:

````markdown
For localhost-only experiments, Origin and Host allowlists provide the first
HTTP boundary. If you bind outside `127.0.0.1`, expose the server through a
proxy, or share the machine with untrusted local users, also configure a static
MCP bearer token:

```bash
printf '%s' '<long-random-local-mcp-token>' > .secrets/modal-mcp-bearer-token
chmod 600 .secrets/modal-mcp-bearer-token
export MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE=$PWD/.secrets/modal-mcp-bearer-token
```
````

**Step 3: Add a local health check section**

Add to `docs/self-hosting.md`:

````markdown
## Local Read-Only Smoke Check

After starting `uv run modal-mcp`, configure an MCP client for:

```text
http://127.0.0.1:8765/mcp
```

Then call:

- `modal_discovery_server_info`
- `modal_whoami`
- `modal_list_environments`
- `modal_list_apps`

Expected:

- Responses use the standard `{ "ok": true, "data": ... }` envelope.
- `mode` is `self_hosted_byo_token`.
- `read_only` is `true`.
- `modal_stop_app`, `modal_rollback_app`, `modal_stop_container`,
  `modal_terminate_sandbox`, and `modal_expert_execute` are absent from
  default `tools/list`.
````

**Step 4: Add the explicit unsupported list**

Keep this concise:

```markdown
This local v1 path does not support hosted OAuth, `/session/create`, hosted
multi-tenant Modal credential storage, or mutating operations.
```

**Step 5: Run docs-adjacent checks**

Run:

```bash
just actionlint
git diff --check
```

Expected: PASS.

**Step 6: Commit**

```bash
git add README.md docs/self-hosting.md
git commit -s -m "docs: clarify local read-only quickstart"
```

## Task 5: Document Opt-In Live Read-Only Verification

**Files:**
- Modify: `docs/self-hosting.md`
- Modify: `tests/integration/live/test_modal_live.py` only if a future reviewer requires broader live inventory coverage

**Step 1: Verify current live test scope**

Run without live env:

```bash
uv run pytest tests/integration/live/test_modal_live.py -q
```

Expected: SKIPPED unless `MODAL_MCP_LIVE=1` is set.

**Step 2: Keep live smoke conservative**

The existing live smoke checks credentials, environments, and apps. That is enough for local read-only readiness.

Only extend it if a future reviewer asks for broader live inventory coverage. Any extension must tolerate empty accounts and unsupported optional surfaces. If extension is required, use only low-risk inventory calls:

```python
adapter.list_volumes(environment)
adapter.list_sandboxes(environment, include_finished=False)
```

Do not add volume file reads, log content reads, sandbox stdio reads, or assertions that require specific account contents. The live test must pass when there are zero apps, volumes, or sandboxes.

**Step 3: Document the live command**

In `docs/self-hosting.md`, add:

````markdown
For maintainers with a safe non-production Modal workspace:

```bash
MODAL_MCP_LIVE=1 \
MODAL_ENVIRONMENT=dev \
MODAL_TOKEN_ID=... \
MODAL_TOKEN_SECRET=... \
MODAL_MCP_SIGNING_KEYS=kid1:... \
uv run pytest tests/integration/live/test_modal_live.py -q
```

This test performs inventory-style read-only calls only. Do not run it with
production credentials.
````

**Step 4: Run local non-live validation**

Run:

```bash
uv run pytest tests/integration/live/test_modal_live.py -q
```

Expected: SKIPPED.

**Step 5: Commit**

```bash
git add docs/self-hosting.md tests/integration/live/test_modal_live.py
git commit -s -m "docs: add opt-in live read-only checklist"
```

## Task 6: Final Readiness Verification

**Files:**
- No planned edits

**Step 1: Run focused readiness checks**

Run:

```bash
just read-only-smoke
```

Expected: PASS.

**Step 2: Run full local gate**

Run:

```bash
just pre-push
```

Expected: PASS, including Gitleaks as the local secret scanner.

**Step 3: Check docs consistency**

Run:

```bash
colgrep -e "uv run modal-mcp" -F "documented local server startup" README.md docs
colgrep -e "MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE" -F "local bearer token documentation" README.md docs
colgrep -e "hosted" -F "unsupported hosted local read only mode" README.md docs
git diff --check
```

Expected:

- Startup command appears only where it is accurate.
- Bearer-token guidance exists for non-localhost or shared-machine use.
- Hosted mode is documented as unsupported for v1 local readiness.
- No whitespace errors.

**Step 4: Check worktree state**

Run:

```bash
git status --short --branch
```

Expected: clean worktree, branch ahead by the planned commits.

**Step 5: Push and check PR**

Run:

```bash
git push origin feat/init-version
gh pr checks 1 --repo php-workx/modal-mcp --watch --interval 10
```

Expected:

- GitHub Actions pass.
- Secret Scan remains TruffleHog in CI.
- Local hooks continue to use Gitleaks.

## Ticket Decomposition

Recommended `tk` tickets, each small enough for one PR or one focused commit group:

1. **P1: Start server from modal-mcp CLI**
   - Fix `src/modal_mcp/__main__.py`.
   - Add `tests/unit/test_cli_entrypoint.py`.
   - Add `tests/unit/test_server_run.py`.

2. **P1: Smoke-test local read-only MCP serving**
   - Add lifespan-aware ASGI/FastMCP initialize smoke coverage.
   - Keep default read-only `tools/list` assertions explicit.

3. **P2: Add local read-only smoke validation target**
   - Add `just read-only-smoke`.
   - Keep it separate from `pre-push` while `pre-push` already runs full tests.

4. **P2: Correct local quickstart documentation**
   - Make README and self-hosting commands executable for both existing Modal config and file-backed service-user tokens.
   - Clarify endpoint, credential scope, and optional MCP bearer-token protection.

5. **P3: Document opt-in live read-only verification**
   - Keep live checks skipped by default.
   - Document safe non-production usage.
   - Avoid content reads and account-content-dependent assertions.

## PR Slicing

Smallest safe PR sequence:

1. **PR 1: CLI startup + startup wrapper tests**
   - Enables the documented command to start the server.
   - Verifies `server.run()` bind parsing and Uvicorn handoff without sockets.

2. **PR 2: Local MCP smoke coverage + `just` target**
   - Proves the local server path and read-only tools are usable.
   - Adds a fast targeted regression check without duplicating `pre-push`.

3. **PR 3: Quickstart and live-readiness docs**
   - Aligns operator docs with implementation.
   - Clarifies credential setup, local bearer-token guidance, and live smoke expectations.

If we want one PR instead, keep commits grouped exactly by the tasks above.

## Risks and Mitigations

- **Risk:** FastMCP HTTP initialize smoke is brittle because Streamable HTTP requires lifespan-managed session state.
  **Mitigation:** Always enter `app.router.lifespan_context(app)` in ASGI smoke tests. If FastMCP changes protocol behavior, use its supported client/session helper rather than weakening production code.

- **Risk:** CLI startup tests accidentally open sockets or require real Modal credentials.
  **Mitigation:** Mock `uvicorn.run` and `create_asgi_app` in unit tests. Leave real process/network checks out of the default suite.

- **Risk:** Quickstart still assumes hidden local state.
  **Mitigation:** Document both credential paths: existing `~/.modal.toml` and explicit file-backed service-user token files.

- **Risk:** Local HTTP access is under-protected when bound beyond localhost.
  **Mitigation:** Document `MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE` for non-localhost, proxy, or shared-machine scenarios.

- **Risk:** Live Modal tests become account-content dependent.
  **Mitigation:** Keep live smoke to inventory calls: auth, environments, apps, optionally volumes/sandboxes list. Never add content reads to mandatory live smoke.

- **Risk:** Docs imply production readiness.
  **Mitigation:** Repeat that v1 local readiness means self-hosted, read-only, restricted service-user credentials, and a non-production environment.

- **Risk:** Mutation approval work distracts from local read-only readiness.
  **Mitigation:** Treat approval, hosted auth, and session-scoped adapters as separate later epics.

## Done Checklist

- [ ] `uv run modal-mcp` delegates to `server.run()`.
- [ ] `server.run()` bind and Uvicorn handoff test passes without sockets.
- [ ] CLI entrypoint unit test passes.
- [ ] Local MCP initialize smoke test enters ASGI lifespan and passes.
- [ ] Default tool list contains only read-only tools.
- [ ] Mutating and expert tools remain hidden/blocked.
- [ ] `just read-only-smoke` exists and passes.
- [ ] `just pre-push` passes.
- [ ] README quickstart is executable with either existing Modal config or explicit file-backed service-user credentials.
- [ ] Self-hosting docs describe the local smoke path.
- [ ] Self-hosting docs describe optional static bearer-token protection.
- [ ] Live Modal smoke remains opt-in, non-production only, and content-read free.
