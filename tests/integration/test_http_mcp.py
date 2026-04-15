"""Integration tests for FastMCP ASGI composition."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.asgi import OriginGuard
from modal_mcp.config import Settings
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

    def list_environments(self) -> list[Environment]:
        return [
            Environment(
                environment_ref="mref1.env",
                name="prod",
                is_default=True,
            )
        ]

    def get_environment(self, environment_name: str) -> Environment | None:
        return self.list_environments()[0] if environment_name == "prod" else None

    def list_apps(self, environment_name: str | None = None) -> list[App]:
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
        ]

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
    ) -> list[Container]:
        del environment_name, app_id
        return [
            Container(
                container_ref="mref1.container",
                task_id="ta-1",
                state="running",
            )
        ]

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

    def read_volume_text(self, volume_id: str, path: str) -> str:
        del volume_id, path
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

    route = app.routes[0]
    assert getattr(route, "path", None) == ""
    mcp_app = route.app
    assert mcp_app.state.path == "/mcp"
    assert any(middleware.cls is OriginGuard for middleware in mcp_app.user_middleware)
    assert app.router.lifespan_context is mcp_app.lifespan


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
