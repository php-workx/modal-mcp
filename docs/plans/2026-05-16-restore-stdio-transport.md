# Restore Dropped Stdio Transport for Codex Subprocess Launch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the `modal-mcp stdio` subcommand and `server.run_stdio()` entry point so that Codex installs of `modal-mcp` (which spawn the binary as a stdio subprocess) actually speak the MCP stdio transport instead of starting a uvicorn HTTP server on `127.0.0.1:8765` and timing out the Codex handshake.

**Architecture:** Two launch modes. `modal-mcp run` keeps starting HTTP/uvicorn for Claude Desktop and browser/SSE clients. A new `modal-mcp stdio` subcommand calls a new `server.run_stdio()` that builds a FastMCP instance with the Modal adapter lifespan + toolset filtering + read-only posture and dispatches `mcp.run(transport="stdio")`. The stdio path skips uvicorn, `OriginGuard`, `allowed_hosts`, bearer auth, and the `/mcp/approvals/{token}` HTTP route — none apply to a stdio subprocess. `CODEX_SERVER_ARGS_TEMPLATE` flips its first element from `"run"` to `"stdio"`; four docstring/dry-run sites in the Codex target update in lockstep. A new cross-module invariant test asserts the first arg resolves to an actually-registered argparse subcommand, so a future refactor cannot silently re-introduce the regression.

**Tech Stack:** Python 3.12, FastMCP, argparse, pytest, ruff, uv.

**Approach decision (cherry-pick vs reapply):** Branch `feat/init-release-v2` has landed several deepening epics after the dropped commits, modifying three of the four files touched. **Decision:** reapply manually using exact code blocks from this plan. Step 1 still inspects the dropped diffs as a sanity check; if a `git cherry-pick` dry-run is clean the agent MAY take the shortcut.

---

## File Structure

Files touched by this plan (repo-relative paths only):

```text
src/modal_mcp/__main__.py                  ← add `stdio` subparser, `_cmd_stdio()` handler, dispatch entry
src/modal_mcp/server.py                    ← add `run_stdio()`, export from __all__
src/modal_mcp/agent_targets/codex.py       ← flip CODEX_SERVER_ARGS_TEMPLATE first token; update 4 docstring/dry-run sites
src/modal_mcp/agent_config.py              ← no source change required (its codex path reads CODEX_SERVER_ARGS_TEMPLATE indirectly via format_config_snippet)
tests/unit/test_agent_config_codex.py      ← flip 3 test assertions from "run" to "stdio"
tests/unit/test_cli_entrypoint.py          ← add stdio subparser registration + dispatch tests
tests/unit/test_server_run.py              ← add run_stdio() coverage (separate from run() HTTP test)
tests/unit/test_codex_args_invariant.py    ← NEW: cross-module regression catcher
docs/clients.md                            ← Codex section shows `stdio` (subprocess) args; keep Claude Desktop on `run` (HTTP/SSE)
README.md                                  ← Codex section shows `stdio` args
```

No new `src/` modules. No `agent_config.py` source change — its Codex flows render the args template via `format_config_snippet()`, which reads `CODEX_SERVER_ARGS_TEMPLATE` at call time, so flipping the constant propagates automatically. (Its `modal-mcp run` docstrings refer to Claude Desktop HTTP startup — keep them as `run`.)

---

## Step 1 — Inspect dropped commits and confirm reapply scope

This step is documentation/verification only. It produces no edits. Its purpose is to make the agent confirm with its own eyes that the commits referenced below still exist in the local `.git/objects` store and match the diffs reproduced in steps 2–5.

- [ ] Confirm both dropped commits are reachable from the local object store (they should still be visible via `git show`, even though they are not ancestors of HEAD):

```bash
cd "$(git rev-parse --show-toplevel)" && git show 827e75e --stat && echo "---" && git show 60a13ca --stat
```

Expected stat lines:

```text
src/modal_mcp/__main__.py             | 32 ++++++++++++++++++++++++++++++++
src/modal_mcp/agent_targets/codex.py  |  6 +++++-
src/modal_mcp/server.py               | 34 ++++++++++++++++++++++++++++++++++
tests/unit/test_agent_config_codex.py | 14 +++++++-------
4 files changed, 78 insertions(+), 8 deletions(-)
---
src/modal_mcp/agent_targets/codex.py | 4 ++--
1 file changed, 2 insertions(+), 2 deletions(-)
```

- [ ] If both commits are reachable, optionally attempt a dry-run cherry-pick to see whether they apply cleanly on current HEAD:

```bash
cd "$(git rev-parse --show-toplevel)" && git cherry-pick --no-commit 827e75e 60a13ca && git status --short && git cherry-pick --abort 2>/dev/null || true
```

- [ ] **Decision rule:** if the cherry-pick reports zero conflicts (`git status --short` shows only `M` lines for the four files, no `UU` / `AA` markers), the agent MAY commit the cherry-pick and skip to Step 6 (verification). Otherwise — and as the safe default — abort the cherry-pick and follow Steps 2–5 to reapply manually using the exact code blocks below. This plan assumes the manual path.

- [ ] Inspect the new `__main__.py` block that the dropped commit added (this is the source-of-truth diff for Step 2):

```bash
cd "$(git rev-parse --show-toplevel)" && git show 827e75e -- src/modal_mcp/__main__.py
```

- [ ] Inspect the new `server.py` block that the dropped commit added (this is the source-of-truth diff for Step 3):

```bash
cd "$(git rev-parse --show-toplevel)" && git show 827e75e -- src/modal_mcp/server.py
```

- [ ] Inspect the constant + docstring changes that the two dropped commits made to `codex.py`:

```bash
cd "$(git rev-parse --show-toplevel)" && git show 827e75e -- src/modal_mcp/agent_targets/codex.py && echo "---" && git show 60a13ca -- src/modal_mcp/agent_targets/codex.py
```

- [ ] Inspect the test assertion flips for context (Step 5):

```bash
cd "$(git rev-parse --show-toplevel)" && git show 827e75e -- tests/unit/test_agent_config_codex.py
```

No edits in this step. Proceed to Step 2.

---

## Step 2 — RED: write the cross-module regression-catcher test FIRST

This is the load-bearing test. It must fail on current `main` (because no `stdio` subparser is registered yet) and pass after Steps 3–5. It is intentionally written before any production code so the failing-test contract proves the regression cannot return unnoticed.

- [ ] Create `tests/unit/test_codex_args_invariant.py` with the following exact content:

```python
"""Cross-module invariant: Codex args[0] must be a registered CLI subcommand.

This test is the regression catcher for the May 2026 stdio-drop incident
where ``CODEX_SERVER_ARGS_TEMPLATE`` said ``("run", "--env-file", ...)`` but
``modal-mcp run`` started uvicorn HTTP instead of MCP stdio.  Codex spawned
the binary, expected a JSON-RPC handshake on stdout, and timed out.

The invariant: whatever subcommand the Codex install writes into
``~/.codex/config.toml`` MUST exist in ``modal-mcp``'s argparse parser AND
MUST be a transport suitable for stdio subprocess launch.  This test fails
loudly if anyone changes one side of that contract without the other.
"""

from __future__ import annotations

import argparse

from modal_mcp.__main__ import build_parser
from modal_mcp.agent_targets.codex import CODEX_SERVER_ARGS_TEMPLATE


def _registered_subcommand_names() -> set[str]:
    """Return the set of subcommand names registered on the modal-mcp parser."""
    parser = build_parser()
    names: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    return names


def test_codex_args_first_token_is_a_registered_subcommand() -> None:
    """The first token of CODEX_SERVER_ARGS_TEMPLATE must resolve to a real subcommand.

    If this fails, someone updated the argparse parser without updating
    CODEX_SERVER_ARGS_TEMPLATE (or vice versa). Codex installs will time out
    because the spawned process either fails to parse args or starts the
    wrong transport.
    """
    registered = _registered_subcommand_names()
    first_token = CODEX_SERVER_ARGS_TEMPLATE[0]
    assert first_token in registered, (
        f"CODEX_SERVER_ARGS_TEMPLATE[0]={first_token!r} is not a registered "
        f"modal-mcp subcommand. Registered: {sorted(registered)!r}. "
        "Codex installs will time out on the MCP initialize handshake. "
        "Either restore the missing subcommand or update the args template."
    )


def test_codex_args_first_token_is_a_stdio_transport_subcommand() -> None:
    """The first token must be 'stdio', not 'run'.

    'run' starts uvicorn HTTP. Codex needs MCP stdio transport. Any other
    value is a bug. Pinning the literal here is intentional: it forces the
    author of any future transport-renaming PR to confront the Codex install
    contract head-on.
    """
    assert CODEX_SERVER_ARGS_TEMPLATE[0] == "stdio", (
        f"CODEX_SERVER_ARGS_TEMPLATE[0]={CODEX_SERVER_ARGS_TEMPLATE[0]!r}; "
        "expected 'stdio'. Codex launches modal-mcp as a stdio subprocess, "
        "not as an HTTP server."
    )
```

- [ ] Run the new test against current `main` and confirm it FAILS. This is the RED state of TDD — it proves the regression test catches the current bug:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest -q tests/unit/test_codex_args_invariant.py 2>&1 | tail -30
```

Expected: both tests FAIL.
- `test_codex_args_first_token_is_a_registered_subcommand` fails because `CODEX_SERVER_ARGS_TEMPLATE[0] == "run"` is registered (the test passes on this one) — actually correct: this one PASSES on current main. Document the actual observed outcome in the run log.
- `test_codex_args_first_token_is_a_stdio_transport_subcommand` fails because `CODEX_SERVER_ARGS_TEMPLATE[0] == "run"`, not `"stdio"`.

After Steps 3–5, both tests must pass.

---

## Step 3 — Add `run_stdio()` to `server.py`

- [ ] Open `src/modal_mcp/server.py`. Locate the `run(...)` function (currently the last function definition before `__all__`, around line 455). Append the following `run_stdio` function directly after `run`, before the `__all__` block:

```python
def run_stdio(settings: Settings | None = None) -> None:
    """Run the Modal MCP server over stdin/stdout (stdio transport).

    Used by CLI clients such as Codex that spawn the server as a subprocess
    and communicate via the MCP stdio transport rather than HTTP.  Auth and
    the HTTP approval route are not applicable here; tool filtering and the
    Modal adapter lifespan are preserved.
    """

    resolved_settings = settings or _settings_from_env()
    configure_logging(resolved_settings)

    @asynccontextmanager
    async def lifespan(server: FastMCP[Any]) -> AsyncIterator[None]:
        async with fastmcp_lifespan(server, settings=resolved_settings):
            yield

    mcp: FastMCP[Any] = FastMCP(
        name="modal-mcp",
        version="0.1.0",
        lifespan=lifespan,
    )
    register_toolsets(mcp, resolved_settings)

    disabled_toolsets = ALL_TOOLSETS - set(resolved_settings.modal_mcp_enabled_toolsets)
    if disabled_toolsets:
        mcp.disable(tags=set(disabled_toolsets))
    if resolved_settings.modal_mcp_read_only:
        mcp.disable(tags={"change", "expert"})

    mcp.run(transport="stdio")
```

What `run_stdio` deliberately does NOT do (matches dropped commit verbatim):

- No `assert_runtime_security`, `OriginGuard`, `allowed_hosts`, `build_auth`, `scrub_secret_env` — all HTTP-only concerns; stdio auth is the parent process's responsibility.
- No `_approval_route` mount — Starlette-only; stdio-side approval is a separate epic.
- No `PolicyMiddleware` — not in the dropped commit; adding it is a separate epic.
- `mcp.disable(tags={"change", "expert"})` (HTTP disables only `expert`) — intentional: stdio has no approval route to gate `change` at request time, so it's disabled at gating time instead.

- [ ] Add `run_stdio` to the `__all__` list at the bottom of `server.py`. Replace:

```python
__all__ = [
    "ALL_TOOLSETS",
    "create_asgi_app",
    "create_mcp",
    "fastmcp_lifespan",
    "run",
]
```

with:

```python
__all__ = [
    "ALL_TOOLSETS",
    "create_asgi_app",
    "create_mcp",
    "fastmcp_lifespan",
    "run",
    "run_stdio",
]
```

- [ ] Sanity-check the module imports cleanly:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run python -c "from modal_mcp.server import run_stdio; print(run_stdio.__doc__.splitlines()[0])"
```

Expected output: `Run the Modal MCP server over stdin/stdout (stdio transport).`

---

## Step 4 — Add `stdio` subcommand to `__main__.py`

- [ ] In `src/modal_mcp/__main__.py`, locate the `build_parser()` function and find the block immediately after the `run` subparser definition (between `run_parser.add_argument(...)` ending around line 25 and `# setup subcommand` comment around line 27). Insert this new block:

```python
    # stdio subcommand
    stdio_parser = subparsers.add_parser(
        "stdio",
        help="Start the MCP server using stdio transport (for CLI clients).",
    )
    stdio_parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="Path to a .env file to load before starting the server.",
    )

```

The file now declares `run`, `stdio`, `setup`, `doctor`, and `print-agent-config` in that order.

- [ ] Locate `_cmd_run(...)` and insert `_cmd_stdio` immediately after it (before `_cmd_setup`):

```python
def _cmd_stdio(args: argparse.Namespace) -> int:
    """Start the MCP server over stdin/stdout (stdio transport)."""
    env_file: str | None = getattr(args, "env_file", None)
    if env_file is not None:
        from pathlib import Path

        env_path = Path(env_file)
        if env_path.is_file():
            from dotenv import load_dotenv

            load_dotenv(str(env_path), override=False)
        else:
            print(f"warn: env file not found: {env_path}", file=sys.stderr)

    from modal_mcp.server import run_stdio

    run_stdio()
    return 0
```

This mirrors `_cmd_run` exactly except for the `run_stdio` import target. Keep the import inline (inside the function body) to match the existing pattern — the CLI defers heavy imports until dispatch.

- [ ] Add the `stdio` entry to the `_HANDLERS` dispatch table near the bottom of the file. Replace:

```python
_HANDLERS: dict[str | None, Callable[[argparse.Namespace], int]] = {
    None: _cmd_run,
    "run": _cmd_run,
    "setup": _cmd_setup,
    "doctor": _cmd_doctor,
    "print-agent-config": _cmd_print_agent_config,
}
```

with:

```python
_HANDLERS: dict[str | None, Callable[[argparse.Namespace], int]] = {
    None: _cmd_run,
    "run": _cmd_run,
    "stdio": _cmd_stdio,
    "setup": _cmd_setup,
    "doctor": _cmd_doctor,
    "print-agent-config": _cmd_print_agent_config,
}
```

- [ ] Sanity-check the parser now knows about `stdio`:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run python -c "from modal_mcp.__main__ import build_parser; p=build_parser(); a=p.parse_args(['stdio','--env-file','/tmp/x.env']); print(a.subcommand, a.env_file)"
```

Expected: `stdio /tmp/x.env`.

---

## Step 5 — Flip `CODEX_SERVER_ARGS_TEMPLATE` + update Codex docstrings + test assertions

This step combines the two dropped commits' Codex-target edits into one cohesive change. After this step the cross-module invariant test from Step 2 must pass.

### 5a — Flip the args template constant

- [ ] In `src/modal_mcp/agent_targets/codex.py`, locate line 113 (the `CODEX_SERVER_ARGS_TEMPLATE` definition). Replace:

```python
CODEX_SERVER_ARGS_TEMPLATE: Final[tuple[str, ...]] = ("run", "--env-file", "{env_file}")
```

with:

```python
CODEX_SERVER_ARGS_TEMPLATE: Final[tuple[str, ...]] = (
    "stdio",
    "--env-file",
    "{env_file}",
)
```

### 5b — Update the four docstring / dry-run sites in `codex.py`

These come from the `60a13ca` commit and the `parse_validation_strategy` + `dry_run_description` strings inside `build_contract`. Audit and update each occurrence of the literal `"run"` (referring to the subcommand) inside `codex.py`.

- [ ] In the module docstring `Install contract summary` table (around line 34), replace:

```rst
       ``args = ["run", "--env-file", "<absolute-path>"]`` so that Codex can
```

with:

```rst
       ``args = ["stdio", "--env-file", "<absolute-path>"]`` so that Codex can
```

- [ ] In the module docstring `Generated config block` block (around line 49), replace:

```rst
    args = ["run", "--env-file", "/absolute/path/to/.env"]
```

with:

```rst
    args = ["stdio", "--env-file", "/absolute/path/to/.env"]
```

- [ ] In `build_contract(...)`, locate the `parse_validation_strategy` string (around line 200) and replace:

```python
            f"with command='{command}' and args starting with 'run'; "
```

with:

```python
            f"with command='{command}' and args starting with 'stdio'; "
```

- [ ] In `build_contract(...)`, locate the `dry_run_description` string (around line 204) and replace:

```python
            f"(command: {command}, args: run --env-file <absolute-path>) "
```

with:

```python
            f"(command: {command}, args: stdio --env-file <absolute-path>) "
```

### 5c — Flip the three test assertions in `test_agent_config_codex.py`

The dropped commit `827e75e` flipped three test assertions. Reapply them.

- [ ] In `tests/unit/test_agent_config_codex.py`, find `test_codex_contract_server_args_template_starts_with_run` (around line 188) and replace the whole function with:

```python
def test_codex_contract_server_args_template_starts_with_stdio() -> None:
    """server_args_template must begin with 'stdio' subcommand."""
    args = CODEX_CONTRACT.server_args_template
    assert args[0] == "stdio", "first arg must be the 'stdio' subcommand"
```

- [ ] Find the assertion inside `test_codex_contract_generated_block_is_valid_toml` (around line 530) and replace:

```python
    assert server_table["args"][0] == "run"
```

with:

```python
    assert server_table["args"][0] == "stdio"
```

- [ ] Find `test_format_config_snippet_args_start_with_run` (around line 738) and replace the whole function with:

```python
def test_format_config_snippet_args_start_with_stdio() -> None:
    """The rendered args list must begin with the 'stdio' subcommand."""
    snippet = format_config_snippet()
    parsed = tomllib.loads(snippet)
    args = parsed[CODEX_TOP_LEVEL_KEY][CODEX_SERVER_NAME]["args"]
    assert args[0] == "stdio"
```

### 5d — Run the codex contract suite and the invariant test

- [ ] Confirm the codex target suite is green and the invariant test from Step 2 now passes:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest -q tests/unit/test_agent_config_codex.py tests/unit/test_codex_args_invariant.py
```

Expected: all tests pass. If `test_codex_args_first_token_is_a_stdio_transport_subcommand` still fails, re-check Step 5a (the template constant flip).

---

## Step 6 — Extend `test_cli_entrypoint.py` with stdio subparser + dispatch coverage

The existing CLI test file (`tests/unit/test_cli_entrypoint.py`, ~750 lines) parametrises subcommand registration over `["run", "setup", "doctor", "print-agent-config"]` and has dedicated dispatch tests per subcommand. Add `stdio` to both.

- [ ] In `tests/unit/test_cli_entrypoint.py`, locate `test_subcommand_registered` (around line 57) and update the parametrise list. Replace:

```python
@pytest.mark.parametrize(
    "subcommand",
    ["run", "setup", "doctor", "print-agent-config"],
)
def test_subcommand_registered(subcommand: str) -> None:
```

with:

```python
@pytest.mark.parametrize(
    "subcommand",
    ["run", "stdio", "setup", "doctor", "print-agent-config"],
)
def test_subcommand_registered(subcommand: str) -> None:
```

- [ ] Locate `test_subcommands_visible_in_help` (around line 73) and update the tuple. Replace:

```python
    for subcommand in ("run", "setup", "doctor", "print-agent-config"):
```

with:

```python
    for subcommand in ("run", "stdio", "setup", "doctor", "print-agent-config"):
```

- [ ] Immediately after the existing `'run' subcommand` block (after `test_run_subcommand_accepts_env_file_flag`, before the `'setup' subcommand` header), insert a new section:

```python
# ---------------------------------------------------------------------------
# 'stdio' subcommand
# ---------------------------------------------------------------------------


def test_stdio_subcommand_delegates_to_server_run_stdio() -> None:
    """main(['stdio']) must invoke modal_mcp.server.run_stdio() exactly once."""
    with patch("modal_mcp.server.run_stdio") as mock_run_stdio:
        result = main(["stdio"])
    mock_run_stdio.assert_called_once_with()
    assert result == 0


def test_stdio_subcommand_does_not_call_server_run() -> None:
    """main(['stdio']) must NOT touch modal_mcp.server.run (the HTTP entry).

    Regression guard for the dropped-stdio incident: if the dispatch table
    ever wires 'stdio' to _cmd_run by mistake, Codex installs silently start
    uvicorn and time out the MCP initialize handshake.
    """
    with (
        patch("modal_mcp.server.run") as mock_http_run,
        patch("modal_mcp.server.run_stdio") as mock_stdio_run,
    ):
        result = main(["stdio"])
    mock_stdio_run.assert_called_once_with()
    mock_http_run.assert_not_called()
    assert result == 0


def test_stdio_subcommand_accepts_env_file_flag() -> None:
    """main(['stdio', '--env-file', '/tmp/x.env']) must not raise a parse error."""
    with patch("modal_mcp.server.run_stdio"):
        result = main(["stdio", "--env-file", "/tmp/x.env"])
    assert result == 0


def test_stdio_subcommand_loads_env_file_when_present(tmp_path: Path) -> None:
    """If --env-file points to a real file, dotenv.load_dotenv must be called."""
    env_path = tmp_path / "stdio.env"
    env_path.write_text("MODAL_MCP_DUMMY=1\n", encoding="utf-8")
    with (
        patch("modal_mcp.server.run_stdio"),
        patch("dotenv.load_dotenv") as mock_load,
    ):
        result = main(["stdio", "--env-file", str(env_path)])
    assert result == 0
    mock_load.assert_called_once()
    called_path = mock_load.call_args.args[0]
    assert Path(called_path) == env_path


```

- [ ] Verify CLI tests pass:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest -q tests/unit/test_cli_entrypoint.py
```

Expected: all tests (existing + new) pass. If `test_stdio_subcommand_loads_env_file_when_present` fails because `dotenv.load_dotenv` is imported under a different module path, change the patch target to `modal_mcp.__main__.load_dotenv` and re-run.

---

## Step 7 — Add `run_stdio()` coverage to `test_server_run.py`

The existing `test_server_run.py` covers only `run()` (HTTP/uvicorn). Add a parallel test for `run_stdio()`.

- [ ] Open `tests/unit/test_server_run.py`. Append the following after the existing `test_run_uses_configured_bind_and_asgi_app` test:

```python
def test_run_stdio_invokes_fastmcp_with_stdio_transport() -> None:
    """run_stdio(settings) must construct a FastMCP and call mcp.run(transport='stdio').

    Stdio launch is the Codex subprocess path: there is no uvicorn, no
    OriginGuard, no approval HTTP route. We assert the negative (no uvicorn
    call) alongside the positive (FastMCP.run was called with the right
    transport) so that a future refactor cannot silently re-introduce HTTP
    side-effects on the stdio path.
    """
    fake_settings = MagicMock()
    fake_settings.modal_mcp_enabled_toolsets = ["discovery"]
    fake_settings.modal_mcp_read_only = True

    fake_mcp_instance = MagicMock()

    with (
        patch("modal_mcp.server.FastMCP", return_value=fake_mcp_instance) as mock_cls,
        patch("modal_mcp.server.register_toolsets") as mock_register,
        patch("modal_mcp.server.configure_logging"),
        patch("modal_mcp.server.uvicorn.run") as mock_uvicorn,
    ):
        from modal_mcp.server import run_stdio

        run_stdio(fake_settings)

    mock_cls.assert_called_once()
    mock_register.assert_called_once()
    fake_mcp_instance.run.assert_called_once_with(transport="stdio")
    mock_uvicorn.assert_not_called()


def test_run_stdio_disables_change_and_expert_when_read_only() -> None:
    """Read-only stdio launch must disable both 'change' and 'expert' tagged tools."""
    fake_settings = MagicMock()
    fake_settings.modal_mcp_enabled_toolsets = list(__import__(
        "modal_mcp.server", fromlist=["ALL_TOOLSETS"]
    ).ALL_TOOLSETS)
    fake_settings.modal_mcp_read_only = True

    fake_mcp_instance = MagicMock()

    with (
        patch("modal_mcp.server.FastMCP", return_value=fake_mcp_instance),
        patch("modal_mcp.server.register_toolsets"),
        patch("modal_mcp.server.configure_logging"),
    ):
        from modal_mcp.server import run_stdio

        run_stdio(fake_settings)

    # Inspect the disable() calls — read-only must disable change+expert.
    disabled_tag_sets = [
        call.kwargs.get("tags") for call in fake_mcp_instance.disable.call_args_list
        if "tags" in call.kwargs
    ]
    flat = set().union(*disabled_tag_sets) if disabled_tag_sets else set()
    assert "change" in flat, f"'change' must be disabled on read-only stdio; got {flat!r}"
    assert "expert" in flat, f"'expert' must be disabled on read-only stdio; got {flat!r}"
```

- [ ] Run the server tests:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest -q tests/unit/test_server_run.py
```

Expected: all tests pass.

---

## Step 8 — Documentation alignment

The Codex section currently shows `args = ["run", ...]` in two doc files. Flip both to `stdio`. Leave the Claude Desktop / Claude Code sections untouched — Claude Desktop uses HTTP/SSE (so `run` is correct) and Claude Code's current configuration uses `run` over stdio which is a separate ticket (NOT addressed by this regression repair; see "Out-of-scope" below).

### 8a — `docs/clients.md`

- [ ] In `docs/clients.md`, locate the `## Codex CLI` section's "Manual equivalent" TOML block (around line 100). Replace:

```toml
[mcp_servers.modal-mcp]
command = "modal-mcp"
args = ["run", "--env-file", "/absolute/path/to/project/.env"]
```

with:

```toml
[mcp_servers.modal-mcp]
command = "modal-mcp"
args = ["stdio", "--env-file", "/absolute/path/to/project/.env"]
```

### 8b — `README.md`

- [ ] In `README.md`, the "Connect An Agent → Codex CLI" section (around line 162) does not embed an explicit `args = [...]` line — it only shows the `setup --install codex` invocation, which already routes through `CODEX_SERVER_ARGS_TEMPLATE` and will pick up the flipped constant automatically. No README edit is required for Codex.

- [ ] Sanity-grep to confirm no Codex-targeted snippet anywhere in `docs/` or `README.md` still says `args = ["run"`:

```bash
cd "$(git rev-parse --show-toplevel)" && colgrep -e 'args = \["run"' "codex stdio args literal" docs/ README.md 2>&1 | head -10
```

Expected: no matches under any `## Codex` heading. Matches under Claude headings are acceptable and intentional (Claude Code uses `run` per its currently-shipped config; a separate ticket would address whether Claude Code should also switch to stdio).

### 8c — Out-of-scope

`docs/clients.md` Claude Code section keeps `args = ["run", ...]` — the dropped commits did not touch it, and whether Claude Code should switch to stdio is a separate product decision, not this regression repair.

---

## Step 9 — Final lint + targeted pytest + smoke test

- [ ] Lint:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run ruff check .
```

Expected: no errors.

- [ ] Targeted test run for everything touched by this plan:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest -q \
    tests/unit/test_cli_entrypoint.py \
    tests/unit/test_agent_config_codex.py \
    tests/unit/test_server_run.py \
    tests/unit/test_codex_args_invariant.py
```

Expected: all tests pass.

- [ ] Full unit suite (catches any cross-file regressions from the docstring updates in `codex.py`):

```bash
cd "$(git rev-parse --show-toplevel)" && uv run pytest -q tests/unit/
```

Expected: all tests pass.

- [ ] **Smoke test the stdio dispatch end-to-end.** This is the single best proof that the regression is fixed — it sends a real MCP JSON-RPC `tools/list` request on stdin and confirms the server replies on stdout. (Requires a working `.env` with Modal credentials available; if none, skip this step and rely on the unit suite.)

```bash
cd "$(git rev-parse --show-toplevel)" && echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | uv run modal-mcp stdio --env-file .env | head -5
```

Expected: stdout contains a JSON line beginning with `{"jsonrpc":"2.0"`. If the process hangs (no stdout reply), the stdio transport is broken — re-check Step 3 (the `mcp.run(transport="stdio")` call) and the import path of `run_stdio` from `_cmd_stdio`.

- [ ] Confirm the Codex install snippet now renders with `stdio`:

```bash
cd "$(git rev-parse --show-toplevel)" && uv run modal-mcp print-agent-config --target codex --env-file /tmp/example.env 2>&1 | grep -E 'args ='
```

Expected: a line like `args = ["stdio", "--env-file", "/tmp/example.env"]`.

- [ ] Optional: `uv run pytest -q tests/integration/test_http_mcp.py` to confirm HTTP composition is unaffected (requires uvicorn + free port).

---

## Step 10 — Commit

- [ ] Stage and commit. Use one commit (this is a regression repair; a tight bisect target is more valuable than commit-history fidelity to the original two dropped commits):

```bash
cd "$(git rev-parse --show-toplevel)" && git add \
    src/modal_mcp/__main__.py \
    src/modal_mcp/server.py \
    src/modal_mcp/agent_targets/codex.py \
    tests/unit/test_cli_entrypoint.py \
    tests/unit/test_agent_config_codex.py \
    tests/unit/test_server_run.py \
    tests/unit/test_codex_args_invariant.py \
    docs/clients.md && \
git commit -m "$(cat <<'EOF'
fix(stdio): restore dropped stdio transport for Codex subprocess launch

Codex spawns modal-mcp as a stdio subprocess and waits for a JSON-RPC
initialize reply on stdout. The previous CODEX_SERVER_ARGS_TEMPLATE
launched `modal-mcp run` which starts uvicorn HTTP and never writes to
stdout — Codex installs timed out on the MCP handshake.

Restore the `modal-mcp stdio` subcommand and `server.run_stdio()` entry
point that were lost during the feat/init-release-v2 branch history.
Flip CODEX_SERVER_ARGS_TEMPLATE first token from "run" to "stdio".
Add a cross-module invariant test that fails loudly if the args
template ever drifts from a registered argparse subcommand again.

Closes epo-restore-dropped-stdio-transport--97nx
EOF
)"
```

---

## Self-review checklist

### Spec coverage

| Acceptance criterion | Covered by |
|---|---|
| `modal-mcp stdio --env-file <path>` is a registered subcommand | Step 4 (subparser) + Step 6 (`test_subcommand_registered`) |
| `_cmd_stdio` dispatches to `server.run_stdio()`, NOT `server.run()` | Step 4 (dispatch table) + Step 6 (`test_stdio_subcommand_does_not_call_server_run`) |
| `server.run_stdio()` constructs FastMCP and calls `mcp.run(transport="stdio")` | Step 3 + Step 7 (`test_run_stdio_invokes_fastmcp_with_stdio_transport`) |
| `run_stdio` skips uvicorn, OriginGuard, allowed_hosts, approval HTTP route | Step 3 (explicit non-inclusion) + Step 7 (`mock_uvicorn.assert_not_called`) |
| `run_stdio` preserves Modal adapter lifespan, tool filtering, read-only posture | Step 3 (uses `fastmcp_lifespan` + `register_toolsets` + `disable(tags=...)`) + Step 7 (`test_run_stdio_disables_change_and_expert_when_read_only`) |
| `CODEX_SERVER_ARGS_TEMPLATE[0] == "stdio"` | Step 5a + Step 5d (`test_codex_contract_server_args_template_starts_with_stdio`) |
| All Codex docstrings and dry-run descriptions reference `stdio` | Step 5b (four explicit replacements) |
| `format_config_snippet()` renders `args = ["stdio", ...]` | Step 5c (`test_format_config_snippet_args_start_with_stdio`) |
| `docs/clients.md` Codex section shows `stdio` args | Step 8a |
| Cross-module regression catcher: args template first token must be a registered subcommand | Step 2 (`test_codex_args_first_token_is_a_registered_subcommand`) |
| Lint passes | Step 9 (`uv run ruff check .`) |
| Targeted + full unit suite passes | Step 9 |
| End-to-end stdio JSON-RPC smoke test | Step 9 |

### Type consistency

- `run_stdio` and `_cmd_stdio` signatures mirror `run` and `_cmd_run` exactly.
- `CODEX_SERVER_ARGS_TEMPLATE: Final[tuple[str, ...]]` annotation unchanged; only the value flips.
- Dispatch table type `dict[str | None, Callable[[argparse.Namespace], int]]` unchanged.

### Cross-cutting invariants

- **Read-only posture preserved.** Step 3 keeps `mcp.disable(tags={"change", "expert"})` when read-only — matches the dropped commit. HTTP disables only `{"expert"}` because the approval route mediates `change` at request time; stdio has no approval route, so `change` is disabled at gating time instead.
- **Tool filtering + adapter lifespan preserved.** `register_toolsets` and `fastmcp_lifespan` are called identically on both paths.
- **No HTTP security checks leak into stdio.** Step 3 explicitly does not call `assert_runtime_security`, `OriginGuard`, `scrub_secret_env`, or `build_auth`. Step 7 asserts `uvicorn.run` is never called.

### Why not extract `run_stdio` further?

A more principled refactor would parametrise `create_mcp` with a `transport` argument. **Out of scope:** this is a regression repair; faithful reproduction of the dropped commit minimises ambiguity. A separate deepening epic can refactor after this ships.
