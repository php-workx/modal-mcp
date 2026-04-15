"""FastMCP server and ASGI composition for Modal MCP."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount

from modal_mcp.adapters.modal_adapter import ModalSdkAdapter
from modal_mcp.adapters.registry import bind_modal_adapter
from modal_mcp.asgi import OriginGuard
from modal_mcp.auth import build_auth
from modal_mcp.config import Settings, assert_runtime_security, scrub_secret_env
from modal_mcp.observability.audit import audit_sink_from_settings
from modal_mcp.observability.logger import configure_logging
from modal_mcp.policy.engine import PolicyMiddleware

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

AdapterFactory = Callable[[Settings], Awaitable[Any]]
SettingsFactory = Callable[[], Settings]


async def _default_adapter_factory(settings: Settings) -> ModalSdkAdapter:
    """Create the production Modal SDK adapter."""

    return await ModalSdkAdapter.create(settings)


def _split_bind(bind: str) -> tuple[str, int]:
    host, separator, port_text = bind.rpartition(":")
    if not separator or not host or not port_text:
        msg = "MODAL_MCP_HTTP_BIND must be formatted as host:port"
        raise ValueError(msg)
    return host, int(port_text)


def _settings_from_env() -> Settings:
    settings_factory = cast(SettingsFactory, Settings)
    return settings_factory()


@asynccontextmanager
async def fastmcp_lifespan(
    server: FastMCP[Any],
    *,
    settings: Settings,
    adapter_factory: AdapterFactory = _default_adapter_factory,
) -> AsyncIterator[None]:
    """Bind the process-wide Modal adapter for the FastMCP lifespan."""

    del server
    adapter = await adapter_factory(settings)
    bind_modal_adapter(adapter)
    try:
        yield
    finally:
        bind_modal_adapter(None)
        close = getattr(adapter, "aclose", None)
        if close is not None:
            result = close()
            if isinstance(result, Awaitable):
                await result


def create_mcp(
    settings: Settings | None = None,
    *,
    adapter_factory: AdapterFactory = _default_adapter_factory,
) -> FastMCP[Any]:
    """Create the FastMCP server with auth, lifespan, and toolset gating."""

    resolved_settings = settings or _settings_from_env()
    configure_logging(resolved_settings)

    @asynccontextmanager
    async def lifespan(server: FastMCP[Any]) -> AsyncIterator[None]:
        async with fastmcp_lifespan(
            server,
            settings=resolved_settings,
            adapter_factory=adapter_factory,
        ):
            yield

    mcp: FastMCP[Any] = FastMCP(
        name="modal-mcp",
        version="0.1.0",
        lifespan=lifespan,
        auth=build_auth(resolved_settings),
    )
    mcp.add_middleware(
        PolicyMiddleware(
            resolved_settings,
            audit_sink=audit_sink_from_settings(resolved_settings),
        )
    )

    disabled_toolsets = ALL_TOOLSETS - set(resolved_settings.modal_mcp_enabled_toolsets)
    if disabled_toolsets:
        mcp.disable(tags=set(disabled_toolsets))
    if resolved_settings.modal_mcp_read_only:
        mcp.disable(tags={"change", "expert"})
    return mcp


def create_asgi_app(
    settings: Settings | None = None,
    *,
    adapter_factory: AdapterFactory = _default_adapter_factory,
) -> Starlette:
    """Create the externally mounted Starlette ASGI application."""

    resolved_settings = settings or _settings_from_env()
    assert_runtime_security(resolved_settings)
    scrub_secret_env(resolved_settings)
    mcp = create_mcp(resolved_settings, adapter_factory=adapter_factory)
    middleware = [Middleware(OriginGuard, settings=resolved_settings)]
    mcp_app = mcp.http_app(path="/mcp", middleware=middleware)
    return Starlette(routes=[Mount("/", app=mcp_app)], lifespan=mcp_app.lifespan)


def run(settings: Settings | None = None) -> None:
    """Run the Modal MCP server with uvicorn."""

    resolved_settings = settings or _settings_from_env()
    host, port = _split_bind(resolved_settings.modal_mcp_http_bind)
    uvicorn.run(create_asgi_app(resolved_settings), host=host, port=port)


__all__ = [
    "ALL_TOOLSETS",
    "create_asgi_app",
    "create_mcp",
    "fastmcp_lifespan",
    "run",
]
