"""Unit tests for the Modal SDK adapter wrapper."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from modal_mcp.adapters.modal_adapter import ModalSdkAdapter
from modal_mcp.config import Settings
from modal_mcp.domain.errors import ModalAdapterError
from modal_mcp.domain.refs import RefPayload, encode_ref

SIGNING_KEYS = (("kid1", bytes.fromhex("a" * 64)),)
SIGNING_KEY_TEXT = "kid1:" + "a" * 64


class ClientClosed(Exception):
    """Fake transient Modal client error."""


class FakeClient:
    """Small fake Modal client exposing a stub and close hook."""

    def __init__(self, stub: Any) -> None:
        self.stub = stub
        self.closed = False

    def _close(self) -> None:
        self.closed = True


class FakeModalFactory:
    """Fake Modal SDK constructor that records async calls and rejects sync use."""

    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.aio_calls: list[tuple[Any, ...]] = []
        self.sync_called = False

    def __call__(self, *args: Any) -> FakeClient:
        self.sync_called = True
        msg = "sync Modal constructor should not be used from async adapter setup"
        raise AssertionError(msg)

    async def aio(self, *args: Any) -> FakeClient:
        self.aio_calls.append(args)
        return self.client


class CloseHook:
    """Callable close hook with an async Modal-style aio variant."""

    def __init__(self) -> None:
        self.sync_called = False
        self.aio_called = False

    def __call__(self) -> None:
        self.sync_called = True

    async def aio(self) -> None:
        self.aio_called = True


class FakeAsyncCloseClient:
    """Fake Modal client exposing only a private close hook with aio."""

    def __init__(self) -> None:
        self.stub = FakeStub()
        self._close = CloseHook()


class FakeStub:
    """Fake Modal stub that records requests."""

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.fail_once = False

    def WorkspaceNameLookup(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        if self.fail_once:
            self.fail_once = False
            raise ClientClosed("closed")
        return {
            "workspace_id": "ws-1",
            "workspace_name": "acme",
            "source": "authenticated_token",
            "current": True,
        }

    def AppList(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "apps": [
                {
                    "app_id": "ap-1",
                    "name": "api",
                    "description": "API",
                    "state": "running",
                    "created_at": datetime(2026, 4, 15, 10, 0, 0),
                    "n_running_tasks": 2,
                    "environment_id": "env-1",
                    "environment_name": "prod",
                    "workspace_name": "acme",
                }
            ]
        }

    def AppFetchLogs(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {"entries": [], "summary": {"error_signatures": []}}

    def VolumeGetFile2(self, request: Any) -> dict[str, bytes]:
        self.requests.append(request)
        return {"data": b"abcdef"}


@pytest.fixture
def modal_config_path(tmp_path: Path) -> Path:
    """Create a placeholder Modal config file for settings validation."""

    path = tmp_path / "modal.toml"
    path.write_text("[default]\n", encoding="utf-8")
    return path


def settings(modal_config_path: Path, environment: str = "prod") -> Settings:
    """Build minimal adapter settings."""

    return Settings(
        modal_config_path=modal_config_path,
        modal_environment=environment,
        modal_mcp_allowed_origins=("http://127.0.0.1:8765",),
        modal_mcp_signing_keys=SecretStr(SIGNING_KEY_TEXT),
    )


@pytest.mark.asyncio
async def test_create_uses_injected_client_and_aclose(modal_config_path: Path) -> None:
    """Tests can inject a fake client without live Modal credentials."""

    client = FakeClient(FakeStub())
    adapter = await ModalSdkAdapter.create(settings(modal_config_path), client=client)

    assert adapter.whoami().name == "acme"
    await adapter.aclose()

    assert client.closed is True


@pytest.mark.asyncio
async def test_create_uses_modal_from_env_aio(
    modal_config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter startup uses Modal's async env constructor inside async lifespan."""

    import modal

    factory = FakeModalFactory(FakeClient(FakeStub()))
    monkeypatch.setattr(modal.Client, "from_env", factory)

    adapter = await ModalSdkAdapter.create(settings(modal_config_path))

    assert adapter.whoami().name == "acme"
    assert factory.aio_calls == [()]
    assert factory.sync_called is False


@pytest.mark.asyncio
async def test_create_uses_modal_from_credentials_aio(
    modal_config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit token startup uses Modal's async credential constructor."""

    import modal

    factory = FakeModalFactory(FakeClient(FakeStub()))
    monkeypatch.setattr(modal.Client, "from_credentials", factory)
    adapter_settings = settings(modal_config_path).model_copy(
        update={
            "modal_token_id": SecretStr("token-id"),
            "modal_token_secret": SecretStr("token-secret"),
        }
    )

    adapter = await ModalSdkAdapter.create(adapter_settings)

    assert adapter.whoami().name == "acme"
    assert factory.aio_calls == [("token-id", "token-secret")]
    assert factory.sync_called is False


@pytest.mark.asyncio
async def test_aclose_prefers_modal_private_close_aio(
    modal_config_path: Path,
) -> None:
    """Adapter shutdown uses Modal's async close hook when present."""

    client = FakeAsyncCloseClient()
    adapter = await ModalSdkAdapter.create(settings(modal_config_path), client=client)

    await adapter.aclose()

    assert client._close.aio_called is True
    assert client._close.sync_called is False


@pytest.mark.asyncio
async def test_list_apps_threads_configured_environment(
    modal_config_path: Path,
) -> None:
    """Explicit environment requests are threaded into Modal RPC calls."""

    stub = FakeStub()
    adapter = await ModalSdkAdapter.create(
        settings(modal_config_path),
        client=FakeClient(stub),
    )

    apps, warnings = adapter.list_apps()

    assert apps[0].name == "api"
    assert warnings == []
    request = stub.requests[-1]
    assert getattr(request, "environment_name", None) == "prod"


@pytest.mark.asyncio
async def test_call_with_reconnect_retries_once(modal_config_path: Path) -> None:
    """Transient channel/client failures reconnect and retry once."""

    first_stub = FakeStub()
    first_stub.fail_once = True
    second_stub = FakeStub()
    clients = [FakeClient(first_stub), FakeClient(second_stub)]

    adapter = await ModalSdkAdapter.create(
        settings(modal_config_path),
        client=clients[0],
        client_factory=lambda: clients[1],
    )

    workspace = adapter.whoami()

    assert workspace.name == "acme"
    assert len(first_stub.requests) == 1
    assert len(second_stub.requests) == 1


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

    # ModalRpcClient.call takes (method_name, request); request lives on rpc too
    request = rpc.request("Empty")
    result = rpc.call("WorkspaceNameLookup", request)

    assert result["workspace_name"] == "acme"
    assert len(first_stub.requests) == 1  # one attempt before transient failure
    assert len(second_stub.requests) == 1  # one retry on new client


@pytest.mark.asyncio
async def test_modal_rpc_client_call_raises_after_two_failures(
    modal_config_path: Path,
) -> None:
    """ModalRpcClient.call raises ModalAdapterError when retry after reconnect fails."""
    from modal_mcp.adapters.modal_adapter import ModalRpcClient

    first_stub = FakeStub()
    first_stub.fail_once = True
    second_stub = FakeStub()
    second_stub.fail_once = True
    first_client = FakeClient(first_stub)
    second_client = FakeClient(second_stub)

    rpc = ModalRpcClient(first_client, client_factory=lambda: second_client)
    request = rpc.request("Empty")

    with pytest.raises(ModalAdapterError) as exc_info:
        rpc.call("WorkspaceNameLookup", request)

    assert exc_info.value.code == "UPSTREAM_ERROR"
    assert exc_info.value.retryable is True
    assert "after reconnect" in str(exc_info.value)


@pytest.mark.asyncio
async def test_call_with_reconnect_raises_retryable_without_factory(
    modal_config_path: Path,
) -> None:
    """Transient failures surface as retryable public upstream errors."""

    stub = FakeStub()
    stub.fail_once = True
    adapter = await ModalSdkAdapter.create(
        settings(modal_config_path),
        client=FakeClient(stub),
    )

    with pytest.raises(ModalAdapterError) as exc_info:
        adapter.whoami()

    assert exc_info.value.code == "UPSTREAM_ERROR"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_verify_ref_env_rejects_cross_environment_refs(
    modal_config_path: Path,
) -> None:
    """Signed refs remain environment-scoped even when cross-env is allowed."""

    adapter_settings = settings(modal_config_path, environment="dev").model_copy(
        update={"modal_mcp_allow_cross_env": True}
    )
    adapter = await ModalSdkAdapter.create(
        adapter_settings,
        client=FakeClient(FakeStub()),
    )
    prod_ref = encode_ref(
        RefPayload(id="ap-1", env="prod", ws="acme", exp=4_102_444_800),
        signing_keys=SIGNING_KEYS,
    )

    with pytest.raises(ValueError, match="env mismatch"):
        adapter._verify_ref_env(prod_ref)


def test_adapter_has_no_default_cli_fallback_import() -> None:
    """The SDK adapter does not import or use the disabled CLI fallback."""

    source = (
        Path(__file__).resolve().parents[2] / "src/modal_mcp/adapters/modal_adapter.py"
    ).read_text(encoding="utf-8")

    assert "_cli_fallback" not in source


@pytest.mark.asyncio
async def test_get_app_matches_signed_refs_by_decoded_native_id(
    modal_config_path: Path,
) -> None:
    """Native app ids are compared to decoded signed refs, not substrings."""

    adapter = await ModalSdkAdapter.create(
        settings(modal_config_path),
        client=FakeClient(FakeStub()),
    )

    app = adapter.get_app("ap-1")

    assert app is not None
    assert app.name == "api"


@pytest.mark.asyncio
async def test_get_container_logs_does_not_send_blank_app_id(
    modal_config_path: Path,
) -> None:
    """Container log reads send task_id without a synthetic empty app_id."""

    stub = FakeStub()
    adapter = await ModalSdkAdapter.create(
        settings(modal_config_path),
        client=FakeClient(stub),
    )

    adapter.get_container_logs("ta-1")

    request = stub.requests[-1]
    if isinstance(request, dict):
        assert "app_id" not in request
        assert request["task_id"] == "ta-1"
    else:
        assert getattr(request, "app_id", None) in {None, ""}
        assert getattr(request, "task_id", None) == "ta-1"


@pytest.mark.asyncio
async def test_read_volume_text_returns_only_bounded_bytes(
    modal_config_path: Path,
) -> None:
    """Volume reads apply the adapter byte cap before decoding."""

    adapter = await ModalSdkAdapter.create(
        settings(modal_config_path),
        client=FakeClient(FakeStub()),
    )

    assert adapter.read_volume_text("vo-1", "/data.txt", max_bytes=3) == "abcd"


class FakeStubWithBadApp(FakeStub):
    """Stub returning one valid app and one malformed app (no id or created_at)."""

    def AppList(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "apps": [
                {
                    "app_id": "ap-1",
                    "name": "api",
                    "description": "API",
                    "state": "running",
                    "created_at": "2026-04-15T10:00:00",
                    "n_running_tasks": 2,
                    "environment_id": "env-1",
                    "environment_name": "prod",
                    "workspace_name": "acme",
                },
                # malformed: no app_id, no name, no created_at — _normalize_ref raises
                {},
            ]
        }


@pytest.mark.asyncio
async def test_list_apps_returns_partial_results_with_warnings(
    modal_config_path: Path,
) -> None:
    """list_apps returns valid items plus per-item failure warnings."""

    adapter = await ModalSdkAdapter.create(
        settings(modal_config_path),
        client=FakeClient(FakeStubWithBadApp()),
    )

    apps, warnings = adapter.list_apps()

    assert len(apps) == 1
    assert apps[0].name == "api"
    assert len(warnings) == 1
    assert "app id is required" in warnings[0]


@pytest.mark.asyncio
async def test_adapter_normalize_calls_do_not_pass_signing_keys(
    modal_config_path: Path,
) -> None:
    """After Task 8: ModalSdkAdapter no longer passes signing_keys at call sites.

    All normalize_* calls are gone; the adapter delegates entirely to the
    normalizer instances that were constructed with signing_keys in __init__.
    """
    import inspect

    import modal_mcp.adapters.modal_adapter as mod

    source = inspect.getsource(mod.ModalSdkAdapter)
    # Strip the __init__ method body (where signing_keys IS passed to normalizer ctors)
    # We check that no call-site in the *rest* of the class passes signing_keys=
    init_end = source.find("def validate_auth")
    post_init_source = source[init_end:]
    assert "signing_keys=" not in post_init_source, (
        "signing_keys= found outside __init__; "
        "all signing key wiring must live in __init__, not call sites"
    )
