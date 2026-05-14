"""Integration tests for FastMCP ASGI composition."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from httpx import ASGITransport, AsyncClient
from mcp import types as mt
from pydantic import SecretStr

import modal_mcp.server as server_module
from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.asgi import OriginGuard
from modal_mcp.config import Settings
from modal_mcp.domain.errors import ModalAdapterError
from modal_mcp.domain.models import (
    App,
    Container,
    Environment,
    LogsPage,
    LogSummary,
    SandboxSummary,
    VolumeEntry,
    VolumeSummary,
    Workspace,
)
from modal_mcp.domain.refs import ApprovalPayload, encode_approval
from modal_mcp.policy.approval import ApprovalTokenLedger
from modal_mcp.policy.engine import PolicyMiddleware
from modal_mcp.policy.rules import READ_ONLY_TOOLSETS
from modal_mcp.server import create_asgi_app, create_mcp, fastmcp_lifespan


class FakeAdapter:
    """Fake adapter for lifespan binding tests."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    def whoami(self) -> Workspace:
        return Workspace(
            workspace_ref="mref1.workspace",
            name="main",
            source="local_profile",
            current=True,
        )

    def list_workspaces(self) -> list[Workspace]:
        return [self.whoami()]

    def list_environments(self) -> tuple[list[Environment], list[str]]:
        return [
            Environment(
                environment_ref="mref1.env",
                name="prod",
                is_default=True,
            )
        ], []

    def get_environment(self, environment_name: str) -> Environment | None:
        return self.list_environments()[0] if environment_name == "prod" else None

    def list_apps(
        self, environment_name: str | None = None
    ) -> tuple[list[App], list[str]]:
        del environment_name
        return [
            App(
                app_ref="mref1.app",
                name="api",
                description="api service",
                state="deployed",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                n_running_tasks=1,
                environment_ref="mref1.env",
            )
        ], []

    def get_app(self, app_id: str, environment_name: str | None = None) -> App | None:
        del environment_name
        return self.list_apps()[0] if app_id == "mref1.app" else None

    def list_app_deployments(
        self, app_id: str, environment_name: str | None = None
    ) -> list:
        del app_id, environment_name
        return []

    def get_app_logs(self, app_id: str, **_: object) -> LogsPage:
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

    def list_containers(
        self,
        environment_name: str | None = None,
        app_id: str | None = None,
    ) -> tuple[list[Container], list[str]]:
        del environment_name, app_id
        return [
            Container(
                container_ref="mref1.container",
                task_id="ta-1",
                state="running",
            )
        ], []

    def get_container(self, task_id: str) -> Container | None:
        return self.list_containers()[0] if task_id == "mref1.container" else None

    def get_container_logs(self, task_id: str, **_: object) -> LogsPage:
        del task_id
        return self.get_app_logs("mref1.app")

    def list_volumes(self, environment_name: str | None = None) -> list[VolumeSummary]:
        del environment_name
        return [
            VolumeSummary(
                volume_ref="mref1.volume",
                name="data",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                environment_ref="mref1.env",
            )
        ]

    def ls_volume(
        self,
        volume_id: str,
        path: str = "/",
        *,
        recursive: bool = False,
        max_entries: int | None = None,
    ) -> list[VolumeEntry]:
        del volume_id, path, recursive, max_entries
        return [
            VolumeEntry(
                path="/data.txt",
                type="file",
                mtime=datetime(2026, 1, 1, tzinfo=UTC),
                size_bytes=4,
            )
        ]

    def read_volume_text(
        self,
        volume_id: str,
        path: str,
        *,
        max_bytes: int | None = None,
    ) -> str:
        del volume_id, path, max_bytes
        return "data"

    def stat_volume_path(self, volume_id: str, path: str) -> VolumeEntry | None:
        del volume_id
        return self.ls_volume("mref1.volume")[0] if path == "/data.txt" else None

    def list_sandboxes(
        self,
        environment_name: str | None = None,
        app_id: str | None = None,
        include_finished: bool = False,
    ) -> list[SandboxSummary]:
        del environment_name, app_id, include_finished
        return [
            SandboxSummary(
                sandbox_ref="mref1.sandbox",
                sandbox_id="sb-1",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                status="running",
                tags=[],
            )
        ]

    def get_sandbox(self, sandbox_id: str) -> SandboxSummary | None:
        return self.list_sandboxes()[0] if sandbox_id == "mref1.sandbox" else None

    def get_sandbox_stdio(self, sandbox_id: str) -> tuple[str, str]:
        del sandbox_id
        return "stdout", "stderr"


class FakeAuditSink:
    """Capture approval audit events for assertions."""

    def __init__(self) -> None:
        self.records: list[tuple[str, object]] = []

    def record_approval(self, action: str, record: object) -> None:
        self.records.append((action, record))

    def record_approval_denial(self, error: object, **metadata: object) -> None:
        self.records.append(("denied", SimpleNamespace(error=error, **metadata)))


class FailingAuditSink(FakeAuditSink):
    """Raise when approval audit output is attempted."""

    def record_approval(self, action: str, record: object) -> None:
        del action, record
        raise RuntimeError("audit sink write failed")


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep HTTP composition tests independent from operator env."""

    for key in (
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "MODAL_TOKEN_ID_FILE",
        "MODAL_TOKEN_SECRET_FILE",
        "MODAL_CONFIG_PATH",
        "MODAL_MCP_ALLOWED_ORIGINS",
        "MODAL_MCP_ALLOWED_HOSTS",
        "MODAL_MCP_SIGNING_KEYS",
        "MODAL_MCP_AUTH_MODE",
        "MODAL_MCP_SELF_HOSTED_BEARER_TOKEN_FILE",
        "MODAL_MCP_APPROVAL_LEDGER",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return minimal valid self-hosted settings."""

    modal_config = tmp_path / "modal.toml"
    modal_config.write_text("[default]\n", encoding="utf-8")
    return Settings(
        modal_config_path=modal_config,
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_allowed_hosts=("127.0.0.1", "localhost"),
        modal_mcp_signing_keys=SecretStr("kid1:" + "a" * 64),
    )


@pytest.fixture
def approval_settings(settings: Settings, tmp_path: Path) -> Settings:
    """Return settings configured for approval endpoint coverage."""

    bearer_token = tmp_path / "bearer-token"
    bearer_token.write_text("bearer-token\n", encoding="utf-8")
    return Settings(
        modal_config_path=settings.modal_config_path,
        modal_mcp_allowed_origins=settings.modal_mcp_allowed_origins,
        modal_mcp_allowed_hosts=settings.modal_mcp_allowed_hosts,
        modal_mcp_signing_keys=settings.modal_mcp_signing_keys,
        modal_mcp_self_hosted_bearer_token_file=bearer_token,
        modal_mcp_mutation_rate_limit_seconds=0,
    )


@pytest.mark.asyncio
async def test_fastmcp_lifespan_binds_and_clears_adapter(settings: Settings) -> None:
    """Lifespan binds the process-wide adapter and closes it on shutdown."""

    adapter = FakeAdapter()

    async def adapter_factory(_: Settings) -> FakeAdapter:
        return adapter

    mcp = create_mcp(settings, adapter_factory=adapter_factory)
    with pytest.raises(LookupError):
        get_modal_adapter()

    async with fastmcp_lifespan(
        mcp, settings=settings, adapter_factory=adapter_factory
    ):
        assert get_modal_adapter() is adapter

    assert adapter.closed is True
    with pytest.raises(LookupError):
        get_modal_adapter()


def test_create_asgi_app_mounts_fastmcp_with_origin_guard_first(
    settings: Settings,
) -> None:
    """The ASGI app mounts FastMCP and applies OriginGuard before handling."""

    async def adapter_factory(_: Settings) -> FakeAdapter:
        return FakeAdapter()

    app = create_asgi_app(settings, adapter_factory=adapter_factory)

    approval_route = app.routes[0]
    assert getattr(approval_route, "path", None) == "/mcp/approvals/{token}"
    assert "POST" in getattr(approval_route, "methods", ())

    route = app.routes[1]
    assert getattr(route, "path", None) == ""
    mcp_app = route.app
    assert mcp_app.state.path == "/mcp"
    assert any(middleware.cls is OriginGuard for middleware in mcp_app.user_middleware)
    assert app.router.lifespan_context is mcp_app.lifespan


@pytest.mark.asyncio
async def test_approval_route_state_is_consumed_by_app_composed_policy_middleware(
    approval_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval POST state must be consumed by the actual mounted policy middleware."""

    change_settings = Settings(
        modal_config_path=approval_settings.modal_config_path,
        modal_mcp_allowed_origins=approval_settings.modal_mcp_allowed_origins,
        modal_mcp_allowed_hosts=approval_settings.modal_mcp_allowed_hosts,
        modal_mcp_signing_keys=approval_settings.modal_mcp_signing_keys,
        modal_mcp_self_hosted_bearer_token_file=(
            approval_settings.modal_mcp_self_hosted_bearer_token_file
        ),
        modal_mcp_mutation_rate_limit_seconds=0,
        modal_mcp_read_only=False,
        modal_mcp_enabled_toolsets=READ_ONLY_TOOLSETS | {"change"},
    )
    fake_audit = FakeAuditSink()
    monkeypatch.setattr(server_module, "audit_sink_from_settings", lambda _: fake_audit)

    async def adapter_factory(_: Settings) -> FakeAdapter:
        return FakeAdapter()

    app = create_asgi_app(change_settings, adapter_factory=adapter_factory)
    ledger = app.state.approval_ledger
    assert isinstance(ledger, ApprovalTokenLedger)

    assert app.state.policy_approval_ledger is app.state.approval_ledger
    assert app.state.approval_audit_sink is fake_audit
    assert app.state.policy_audit_sink is fake_audit
    policy_middlewares = [
        middleware
        for middleware in app.state.mcp.middleware
        if isinstance(middleware, PolicyMiddleware)
    ]
    assert len(policy_middlewares) == 1
    policy_middleware = policy_middlewares[0]
    assert policy_middleware.approval_ledger is ledger

    now = int(datetime.now(UTC).timestamp())
    payload = ApprovalPayload(
        tool_name="modal_stop_app",
        target_refs=("mref1.app",),
        actor="self-hosted",
        ws="workspace-1",
        mcp_session_id="mcp-1",
        auth_session_id="self-hosted",
        nonce="nonce-1",
        env="prod",
        exp=now + 3_600,
        nbf=now - 60,
        remote_mode="self_hosted_byo_token",
    )
    token = encode_approval(
        payload,
        signing_keys=(("kid1", bytes.fromhex("a" * 64)),),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = await client.post(
            f"/mcp/approvals/{token}",
            headers={
                "Authorization": "Bearer bearer-token",
                "Host": "localhost:8765",
                "Origin": "http://127.0.0.1:8765",
                "Sec-Fetch-Site": "same-origin",
                "Mcp-Session-Id": "mcp-1",
                "X-Modal-MCP-Confirm-Approval": "approve",
            },
            json={
                "tool_name": "modal_stop_app",
                "workspace": "workspace-1",
                "target_refs": ["mref1.app"],
                "confirmation": "approve",
            },
        )

    assert response.status_code == 200
    assert ledger.is_approved(token) is True

    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="modal_stop_app",
            arguments={
                "dry_run": False,
                "approval_token": token,
                "app_ref": "mref1.app",
            },
        ),
        fastmcp_context=SimpleNamespace(session_id="mcp-1", client_id="self-hosted"),
        method="tools/call",
    )

    async def call_next(
        next_context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> ToolResult:
        assert "approval_token" not in (next_context.message.arguments or {})
        return ToolResult(structured_content={"ok": True})

    result = await policy_middleware.on_call_tool(context, call_next)

    assert result.structured_content == {"ok": True}
    assert ledger.is_approved(token) is False
    assert ledger.is_consumed(token) is True
    with pytest.raises(ModalAdapterError, match="already been consumed"):
        await policy_middleware.on_call_tool(context, call_next)


@pytest.mark.asyncio
async def test_post_mcp_approvals_records_approval_and_audit(
    approval_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid approval request records approval and audit output."""

    fake_audit = FakeAuditSink()
    monkeypatch.setattr(server_module, "audit_sink_from_settings", lambda _: fake_audit)

    async def adapter_factory(_: Settings) -> FakeAdapter:
        return FakeAdapter()

    app = create_asgi_app(approval_settings, adapter_factory=adapter_factory)
    ledger = app.state.approval_ledger
    assert isinstance(ledger, ApprovalTokenLedger)
    now = int(datetime.now(UTC).timestamp())

    payload = ApprovalPayload(
        tool_name="modal_stop_app",
        target_refs=("mref1.app",),
        actor="self-hosted",
        ws="workspace-1",
        mcp_session_id="mcp-1",
        auth_session_id="self-hosted",
        nonce="nonce-1",
        env="prod",
        exp=now + 3_600,
        nbf=now - 60,
        remote_mode="self_hosted_byo_token",
    )
    token = encode_approval(
        payload,
        signing_keys=(("kid1", bytes.fromhex("a" * 64)),),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = await client.post(
            f"/mcp/approvals/{token}",
            headers={
                "Authorization": "Bearer bearer-token",
                "Host": "localhost:8765",
                "Origin": "http://127.0.0.1:8765",
                "Sec-Fetch-Site": "same-origin",
                "Mcp-Session-Id": "mcp-1",
                "X-Modal-MCP-Confirm-Approval": "approve",
            },
            json={
                "tool_name": "modal_stop_app",
                "workspace": "workspace-1",
                "target_refs": ["mref1.app"],
                "confirmation": "approve",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["approval"]["status"] == "approved"
    assert body["approval"]["tool_name"] == "modal_stop_app"
    assert ledger.is_approved(token) is True
    assert len(fake_audit.records) == 1
    assert fake_audit.records[0][0] == "approved"


@pytest.mark.asyncio
async def test_post_mcp_approvals_rolls_back_when_audit_write_fails(
    tmp_path: Path,
    approval_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit failures must not leave an approved token usable."""

    durable_settings = Settings(
        modal_config_path=approval_settings.modal_config_path,
        modal_mcp_allowed_origins=approval_settings.modal_mcp_allowed_origins,
        modal_mcp_allowed_hosts=approval_settings.modal_mcp_allowed_hosts,
        modal_mcp_signing_keys=approval_settings.modal_mcp_signing_keys,
        modal_mcp_self_hosted_bearer_token_file=(
            approval_settings.modal_mcp_self_hosted_bearer_token_file
        ),
        modal_mcp_mutation_rate_limit_seconds=0,
        modal_mcp_approval_ledger=str(tmp_path / "approvals.jsonl"),
    )
    fake_audit = FailingAuditSink()
    monkeypatch.setattr(server_module, "audit_sink_from_settings", lambda _: fake_audit)

    async def adapter_factory(_: Settings) -> FakeAdapter:
        return FakeAdapter()

    app = create_asgi_app(durable_settings, adapter_factory=adapter_factory)
    ledger = app.state.approval_ledger
    assert isinstance(ledger, ApprovalTokenLedger)
    now = int(datetime.now(UTC).timestamp())

    payload = ApprovalPayload(
        tool_name="modal_stop_app",
        target_refs=("mref1.app",),
        actor="self-hosted",
        ws="workspace-1",
        mcp_session_id="mcp-1",
        auth_session_id="self-hosted",
        nonce="nonce-1",
        env="prod",
        exp=now + 3_600,
        nbf=now - 60,
        remote_mode="self_hosted_byo_token",
    )
    token = encode_approval(
        payload,
        signing_keys=(("kid1", bytes.fromhex("a" * 64)),),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = await client.post(
            f"/mcp/approvals/{token}",
            headers={
                "Authorization": "Bearer bearer-token",
                "Host": "localhost:8765",
                "Origin": "http://127.0.0.1:8765",
                "Sec-Fetch-Site": "same-origin",
                "Mcp-Session-Id": "mcp-1",
                "X-Modal-MCP-Confirm-Approval": "approve",
            },
            json={
                "tool_name": "modal_stop_app",
                "workspace": "workspace-1",
                "target_refs": ["mref1.app"],
                "confirmation": "approve",
            },
        )

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_DRIFT"
    assert body["error"]["message"] == "approval endpoint failed"
    assert ledger.is_approved(token) is False
    assert ledger.is_consumed(token) is False
    assert ledger.is_audit_failed(token) is True
    restarted = ApprovalTokenLedger(durable_settings.modal_mcp_approval_ledger)
    assert restarted.is_approved(token) is False
    assert restarted.is_consumed(token) is False
    assert restarted.is_audit_failed(token) is True
    assert fake_audit.records == []


@pytest.mark.asyncio
async def test_post_mcp_approvals_rate_limits_and_audits_denial(
    approval_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval POSTs should use mutation-rate controls and denial audit."""

    rate_limited_settings = Settings(
        modal_config_path=approval_settings.modal_config_path,
        modal_mcp_allowed_origins=approval_settings.modal_mcp_allowed_origins,
        modal_mcp_allowed_hosts=approval_settings.modal_mcp_allowed_hosts,
        modal_mcp_signing_keys=approval_settings.modal_mcp_signing_keys,
        modal_mcp_self_hosted_bearer_token_file=(
            approval_settings.modal_mcp_self_hosted_bearer_token_file
        ),
        modal_mcp_mutation_rate_limit_seconds=30,
    )
    fake_audit = FakeAuditSink()
    monkeypatch.setattr(server_module, "audit_sink_from_settings", lambda _: fake_audit)

    async def adapter_factory(_: Settings) -> FakeAdapter:
        return FakeAdapter()

    app = create_asgi_app(rate_limited_settings, adapter_factory=adapter_factory)
    now = int(datetime.now(UTC).timestamp())

    def token_for(nonce: str) -> str:
        payload = ApprovalPayload(
            tool_name="modal_stop_app",
            target_refs=("mref1.app",),
            actor="self-hosted",
            ws="workspace-1",
            mcp_session_id="mcp-1",
            auth_session_id="self-hosted",
            nonce=nonce,
            env="prod",
            exp=now + 3_600,
            nbf=now - 60,
            remote_mode="self_hosted_byo_token",
        )
        return encode_approval(
            payload,
            signing_keys=(("kid1", bytes.fromhex("a" * 64)),),
        )

    headers = {
        "Authorization": "Bearer bearer-token",
        "Host": "localhost:8765",
        "Origin": "http://127.0.0.1:8765",
        "Sec-Fetch-Site": "same-origin",
        "Mcp-Session-Id": "mcp-1",
        "X-Modal-MCP-Confirm-Approval": "approve",
    }
    json_body = {
        "tool_name": "modal_stop_app",
        "workspace": "workspace-1",
        "target_refs": ["mref1.app"],
        "confirmation": "approve",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        first = await client.post(
            f"/mcp/approvals/{token_for('nonce-1')}",
            headers=headers,
            json=json_body,
        )
        second = await client.post(
            f"/mcp/approvals/{token_for('nonce-2')}",
            headers=headers,
            json=json_body,
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RATE_LIMITED"
    assert [record[0] for record in fake_audit.records] == ["approved", "denied"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_kwargs", "match"),
    [
        ({"headers": {"Authorization": None}}, "authenticated actor"),
        ({"headers": {"Authorization": "Bearer wrong-token"}}, "authenticated actor"),
        (
            {"headers": {"Origin": "https://evil.example.com"}},
            "origin is not allowlisted",
        ),
        (
            {"headers": {"Sec-Fetch-Site": "cross-site"}},
            "cross-site approval requests are rejected",
        ),
        ({"payload_actor": "other-actor"}, "approval token actor mismatch"),
        ({"headers": {"Mcp-Session-Id": "mcp-2"}}, "MCP session mismatch"),
        (
            {
                "headers": {"X-Modal-MCP-Confirm-Approval": None},
                "json": {"confirmation": "maybe"},
            },
            "confirmation",
        ),
        ({"path_suffix": ".tamper"}, "invalid approval token"),
        ({"replay": True}, "already been approved"),
    ],
)
async def test_post_mcp_approvals_rejects_invalid_requests_without_ledger_write(
    approval_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    request_kwargs: dict[str, object],
    match: str,
) -> None:
    """Invalid approval requests fail without writing approval state."""

    fake_audit = FakeAuditSink()
    monkeypatch.setattr(server_module, "audit_sink_from_settings", lambda _: fake_audit)

    async def adapter_factory(_: Settings) -> FakeAdapter:
        return FakeAdapter()

    app = create_asgi_app(approval_settings, adapter_factory=adapter_factory)
    ledger = app.state.approval_ledger
    assert isinstance(ledger, ApprovalTokenLedger)
    now = int(datetime.now(UTC).timestamp())

    payload = ApprovalPayload(
        tool_name="modal_stop_app",
        target_refs=("mref1.app",),
        actor=str(request_kwargs.get("payload_actor", "self-hosted")),
        ws="workspace-1",
        mcp_session_id="mcp-1",
        auth_session_id="self-hosted",
        nonce="nonce-1",
        env="prod",
        exp=now + 3_600,
        nbf=now - 60,
        remote_mode="self_hosted_byo_token",
    )
    token = encode_approval(
        payload,
        signing_keys=(("kid1", bytes.fromhex("a" * 64)),),
    )

    headers: dict[str, str] = {
        "Authorization": "Bearer bearer-token",
        "Host": "localhost:8765",
        "Origin": "http://127.0.0.1:8765",
        "Sec-Fetch-Site": "same-origin",
        "Mcp-Session-Id": "mcp-1",
        "X-Modal-MCP-Confirm-Approval": "approve",
    }
    headers_payload = request_kwargs.get("headers")
    if isinstance(headers_payload, dict):
        for key, value in headers_payload.items():
            if value is None:
                headers.pop(str(key), None)
            else:
                headers[str(key)] = str(value)

    json_body: dict[str, object] = {
        "tool_name": "modal_stop_app",
        "workspace": "workspace-1",
        "target_refs": ["mref1.app"],
        "confirmation": "approve",
    }
    json_payload = request_kwargs.get("json")
    if isinstance(json_payload, dict):
        json_body.update(json_payload)
    request_token = token + str(request_kwargs.get("path_suffix", ""))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        if bool(request_kwargs.get("replay")):
            success = await client.post(
                f"/mcp/approvals/{token}",
                headers=headers,
                json=json_body,
            )
            assert success.status_code == 200
            fake_audit.records.clear()
            response = await client.post(
                f"/mcp/approvals/{token}",
                headers=headers,
                json=json_body,
            )
        else:
            response = await client.post(
                f"/mcp/approvals/{request_token}",
                headers=headers,
                json=json_body,
            )

    assert response.status_code in {400, 401, 403}
    body = response.json()
    assert body["ok"] is False
    assert match in body["error"]["message"]
    if bool(request_kwargs.get("replay")):
        assert ledger.is_approved(token) is True
    else:
        assert ledger.is_approved(token) is False
        assert ledger.is_consumed(token) is False
    assert [record[0] for record in fake_audit.records] == ["denied"]
    denial = fake_audit.records[0][1]
    assert match in denial.error.safe_message


@pytest.mark.asyncio
async def test_tools_list_exposes_read_only_tools_only(settings: Settings) -> None:
    """Default tools/list contains read-only discovery and app tools only."""

    mcp = create_mcp(settings)

    tools = await mcp.list_tools(run_middleware=False)
    names = {tool.name for tool in tools}

    assert {
        "modal_discovery_server_info",
        "modal_whoami",
        "modal_list_workspaces",
        "modal_list_environments",
        "modal_get_environment",
        "modal_list_apps",
        "modal_get_app",
        "modal_list_app_deployments",
        "modal_get_app_logs",
        "modal_search_logs",
        "modal_summarize_failures",
        "modal_compare_deployments",
        "modal_diagnose_app_startup",
        "modal_list_containers",
        "modal_get_container",
        "modal_get_container_logs",
        "modal_list_volumes",
        "modal_ls_volume",
        "modal_read_volume_text",
        "modal_stat_volume_path",
        "modal_list_sandboxes",
        "modal_get_sandbox",
        "modal_get_sandbox_stdio",
    } <= names
    assert "modal_stop_app" not in names
    assert "modal_rollback_app" not in names
    assert "modal_stop_container" not in names
    assert "modal_terminate_sandbox" not in names
    assert "modal_expert_execute" not in names
    assert all(tool.annotations.readOnlyHint is True for tool in tools)


def test_server_uses_registry_not_fastmcp_session_state() -> None:
    """Adapter injection remains process-wide, not ctx.get_state based."""

    source = Path("src/modal_mcp/server.py").read_text(encoding="utf-8")

    assert "bind_modal_adapter" in source
    assert "ctx.get_state" not in source
    assert "ctx.set_state" not in source
    assert 'mcp.http_app(path="/mcp"' in source
    assert "lifespan=mcp_app.lifespan" in source
