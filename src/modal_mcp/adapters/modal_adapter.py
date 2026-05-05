"""Modal SDK-backed adapter implementation."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import SecretStr

from modal_mcp.config import Settings
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.domain.models import (
    App,
    Container,
    Environment,
    LogEntry,
    LogsPage,
    SandboxSummary,
    VolumeEntry,
    VolumeSummary,
    Workspace,
)
from modal_mcp.domain.normalize import (
    normalize_app,
    normalize_container,
    normalize_deployment,
    normalize_environment,
    normalize_log_batch,
    normalize_sandbox,
    normalize_volume,
    normalize_workspace,
)
from modal_mcp.domain.refs import decode_ref

ClientFactory = Callable[[], Any]

TRANSIENT_ERROR_NAMES = frozenset(
    {
        "ClientClosed",
        "DEADLINE_EXCEEDED",
        "ServiceUnavailable",
        "UNAVAILABLE",
        "Unavailable",
    }
)


def _secret_value(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    return value.get_secret_value()


def _parse_signing_keys(raw: SecretStr | None) -> tuple[tuple[str, bytes], ...]:
    text = _secret_value(raw)
    if not text:
        return ()
    keys: list[tuple[str, bytes]] = []
    for item in text.split(","):
        kid, hex_key = item.split(":", 1)
        keys.append((kid.strip(), bytes.fromhex(hex_key.strip())))
    return tuple(keys)


def _is_transient_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in TRANSIENT_ERROR_NAMES:
        return True
    code = getattr(exc, "code", None)
    if callable(code):
        code = code()
    code_name = getattr(code, "name", None) or str(code)
    return code_name.upper() in TRANSIENT_ERROR_NAMES


def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        msg = "async Modal client factories are not supported in sync adapter calls"
        raise TypeError(msg)
    return value


def _items(raw: Any, *names: str) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, tuple):
        return list(raw)
    if isinstance(raw, Mapping):
        for name in names:
            value = raw.get(name)
            if value is not None:
                return _items(value)
    for name in names:
        value = getattr(raw, name, None)
        if value is not None:
            return _items(value)
    if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes, bytearray)):
        return list(raw)
    return [raw]


def _empty_request() -> Any:
    try:
        empty_pb2 = importlib.import_module("google.protobuf.empty_pb2")
    except ImportError:
        return {}
    return empty_pb2.Empty()


class ModalSdkAdapter:
    """Read-only Modal adapter backed by an injected or real Modal client."""

    def __init__(
        self,
        settings: Settings,
        client: Any,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._client_factory = client_factory
        self._signing_keys = _parse_signing_keys(settings.modal_mcp_signing_keys)

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
        return cls(settings, client, client_factory=client_factory)

    @staticmethod
    async def _create_modal_client(settings: Settings) -> Any:
        token_id = _secret_value(settings.modal_token_id)
        token_secret = _secret_value(settings.modal_token_secret)
        try:
            import modal
        except ImportError as exc:  # pragma: no cover - dependency guard
            msg = "Modal SDK is not installed"
            raise ModalAdapterError(ErrorCode.INTERNAL_DRIFT, msg) from exc
        if token_id and token_secret:
            return await modal.Client.from_credentials.aio(token_id, token_secret)
        return await modal.Client.from_env.aio()

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

        close = private_close
        result = close()
        if inspect.isawaitable(result):
            await result

    @property
    def _stub(self) -> Any:
        return getattr(self._client, "stub", self._client)

    def _request(self, request_type: str, **fields: Any) -> Any:
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

    def _call_rpc(self, method_name: str, request: Any | None = None) -> Any:
        method = getattr(self._stub, method_name)
        if request is None:
            return method()
        return method(request)

    def _call_with_reconnect(
        self,
        method_name: str,
        request: Any | None = None,
    ) -> Any:
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

    def _environment_name(self, environment_name: str | None = None) -> str | None:
        return environment_name or self._settings.modal_environment

    def _verify_ref_env(self, ref: str, expected_env: str | None = None) -> str:
        """Decode a public ref and enforce the requested environment."""

        env = expected_env or self._settings.modal_environment
        payload = decode_ref(ref, expected_env=env, signing_keys=self._signing_keys)
        return payload.id

    def _native_id(self, value: str, expected_env: str | None = None) -> str:
        if value.startswith("mref1."):
            return self._verify_ref_env(value, expected_env=expected_env)
        return value

    def validate_auth(self) -> None:
        """Verify that the current client can perform a cheap workspace lookup."""

        self._call_with_reconnect("WorkspaceNameLookup", self._request("Empty"))

    def whoami(self) -> Workspace:
        """Return the authenticated workspace summary."""

        raw = self._call_with_reconnect("WorkspaceNameLookup", self._request("Empty"))
        return normalize_workspace(raw, signing_keys=self._signing_keys)

    def list_workspaces(self) -> Sequence[Workspace]:
        """Return local/current workspace information."""

        return [self.whoami()]

    def list_environments(self) -> Sequence[Environment]:
        """Return environments visible to the authenticated workspace."""

        raw = self._call_with_reconnect("EnvironmentList", self._request("Empty"))
        return [
            normalize_environment(item, signing_keys=self._signing_keys)
            for item in _items(raw, "items", "environments")
        ]

    def get_environment(self, environment_name: str) -> Environment | None:
        """Return a single environment by name."""

        for environment in self.list_environments():
            if environment.name == environment_name:
                return environment
        return None

    def list_apps(self, environment_name: str | None = None) -> Sequence[App]:
        """Return apps visible in an environment."""

        env = self._environment_name(environment_name)
        request = self._request("AppListRequest", environment_name=env)
        raw = self._call_with_reconnect("AppList", request)
        return [
            normalize_app(item, signing_keys=self._signing_keys)
            for item in _items(raw, "apps", "items")
        ]

    def get_app(self, app_id: str, environment_name: str | None = None) -> App | None:
        """Return a single app by public ref, id, or name."""

        native_id = self._native_id(
            app_id,
            expected_env=self._environment_name(environment_name),
        )
        for app in self.list_apps(environment_name):
            if app.app_ref == app_id or app.name == app_id:
                return app
            try:
                app_native_id = decode_ref(
                    app.app_ref,
                    expected_env=self._environment_name(environment_name),
                    signing_keys=self._signing_keys,
                ).id
            except ValueError:
                continue
            if app_native_id == native_id:
                return app
        return None

    def list_app_deployments(
        self,
        app_id: str,
        environment_name: str | None = None,
    ) -> Sequence[Any]:
        """Return normalized deployment history for an app."""

        native_id = self._native_id(
            app_id,
            expected_env=self._environment_name(environment_name),
        )
        request = self._request("AppDeploymentHistoryRequest", app_id=native_id)
        raw = self._call_with_reconnect("AppDeploymentHistory", request)
        return [
            normalize_deployment(item, signing_keys=self._signing_keys)
            for item in _items(raw, "app_deployment_histories", "items")
        ]

    def get_app_logs(
        self,
        app_id: str | None,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        source: str | None = None,
        function_id: str | None = None,
        function_call_id: str | None = None,
        task_id: str | None = None,
        sandbox_id: str | None = None,
        search_text: str | None = None,
    ) -> LogsPage:
        """Return a bounded log page for an app."""

        native_id = self._native_id(app_id) if app_id else None
        request = self._request(
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
        raw = self._call_with_reconnect("AppFetchLogs", request)
        return normalize_log_batch(raw, signing_keys=self._signing_keys)

    async def tail_app_logs(
        self,
        app_id: str,
        *,
        timeout: float = 30.0,  # noqa: ASYNC109
        source: str | None = None,
        function_id: str | None = None,
        function_call_id: str | None = None,
        task_id: str | None = None,
        sandbox_id: str | None = None,
        search_text: str | None = None,
    ) -> AsyncIterator[LogEntry]:
        """Yield log entries from the bounded tail path."""

        del timeout
        page = self.get_app_logs(
            app_id,
            source=source,
            function_id=function_id,
            function_call_id=function_call_id,
            task_id=task_id,
            sandbox_id=sandbox_id,
            search_text=search_text,
        )
        for entry in page.entries:
            yield entry

    def list_containers(
        self,
        environment_name: str | None = None,
        app_id: str | None = None,
    ) -> Sequence[Container]:
        """Return containers for an environment or app."""

        env = self._environment_name(environment_name)
        native_app_id = self._native_id(app_id, expected_env=env) if app_id else None
        request = self._request(
            "TaskListRequest",
            environment_name=env,
            app_id=native_app_id,
        )
        raw = self._call_with_reconnect("TaskList", request)
        return [
            normalize_container(item, signing_keys=self._signing_keys)
            for item in _items(raw, "tasks", "items")
        ]

    def get_container(self, task_id: str) -> Container | None:
        """Return a single container by task id."""

        request = self._request("TaskGetInfoRequest", task_id=self._native_id(task_id))
        raw = self._call_with_reconnect("TaskGetInfo", request)
        items = _items(raw, "tasks", "items")
        if not items:
            return normalize_container(raw, signing_keys=self._signing_keys)
        return normalize_container(items[0], signing_keys=self._signing_keys)

    def get_container_logs(
        self,
        task_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        source: str | None = None,
        function_id: str | None = None,
        function_call_id: str | None = None,
        sandbox_id: str | None = None,
        search_text: str | None = None,
    ) -> LogsPage:
        """Return logs for one container."""

        return self.get_app_logs(
            None,
            since=since,
            until=until,
            limit=limit,
            source=source,
            function_id=function_id,
            function_call_id=function_call_id,
            task_id=self._native_id(task_id),
            sandbox_id=sandbox_id,
            search_text=search_text,
        )

    def list_volumes(
        self,
        environment_name: str | None = None,
    ) -> Sequence[VolumeSummary]:
        """Return volumes visible in an environment."""

        env = self._environment_name(environment_name)
        request = self._request("VolumeListRequest", environment_name=env)
        raw = self._call_with_reconnect("VolumeList", request)
        return [
            normalize_volume(item, signing_keys=self._signing_keys)
            for item in _items(raw, "items", "volumes")
        ]

    def ls_volume(
        self,
        volume_id: str,
        path: str = "/",
        *,
        recursive: bool = False,
        max_entries: int | None = None,
    ) -> Sequence[VolumeEntry]:
        """Return volume entries under a path."""

        request = self._request(
            "VolumeListFiles2Request",
            volume_id=self._native_id(volume_id),
            path=path,
            recursive=recursive,
            max_entries=max_entries,
        )
        raw = self._call_with_reconnect("VolumeListFiles2", request)
        return [
            VolumeEntry.model_validate(item) for item in _items(raw, "entries", "items")
        ]

    def read_volume_text(
        self,
        volume_id: str,
        path: str,
        *,
        encoding: str = "utf-8",
        max_bytes: int | None = None,
    ) -> str:
        """Return a text file from a volume."""

        request = self._request(
            "VolumeGetFile2Request",
            volume_id=self._native_id(volume_id),
            path=path,
        )
        raw = self._call_with_reconnect("VolumeGetFile2", request)
        data = getattr(
            raw,
            "data",
            raw.get("data") if isinstance(raw, Mapping) else raw,
        )
        if isinstance(data, bytes):
            if max_bytes is not None:
                data = data[: max_bytes + 1]
            return data.decode(encoding)
        text = str(data)
        if max_bytes is None:
            return text
        return text.encode(encoding)[: max_bytes + 1].decode(encoding, errors="replace")

    def stat_volume_path(self, volume_id: str, path: str) -> VolumeEntry | None:
        """Return metadata for a single volume path."""

        for entry in self.ls_volume(volume_id, path):
            if entry.path == path:
                return entry
        return None

    def list_sandboxes(
        self,
        environment_name: str | None = None,
        app_id: str | None = None,
        tags: Mapping[str, str] | None = None,
        include_finished: bool = False,
    ) -> Sequence[SandboxSummary]:
        """Return sandboxes visible in an environment or app."""

        env = self._environment_name(environment_name)
        request = self._request(
            "SandboxListRequest",
            environment_name=env,
            app_id=self._native_id(app_id, expected_env=env) if app_id else None,
            tags=dict(tags or {}),
            include_finished=include_finished,
        )
        raw = self._call_with_reconnect("SandboxList", request)
        return [
            normalize_sandbox(item, signing_keys=self._signing_keys)
            for item in _items(raw, "sandboxes", "items")
        ]

    def get_sandbox(self, sandbox_id: str) -> SandboxSummary | None:
        """Return a single sandbox by id."""

        request = self._request(
            "SandboxWaitRequest",
            sandbox_id=self._native_id(sandbox_id),
        )
        raw = self._call_with_reconnect("SandboxWait", request)
        return normalize_sandbox(raw, signing_keys=self._signing_keys)

    def get_sandbox_stdio(self, sandbox_id: str) -> tuple[str, str]:
        """Return buffered stdout/stderr text for a sandbox."""

        request = self._request(
            "SandboxGetLogsRequest",
            sandbox_id=self._native_id(sandbox_id),
        )
        raw = self._call_with_reconnect("SandboxGetLogs", request)
        stdout = getattr(
            raw,
            "stdout",
            raw.get("stdout", "") if isinstance(raw, Mapping) else "",
        )
        stderr = getattr(
            raw,
            "stderr",
            raw.get("stderr", "") if isinstance(raw, Mapping) else "",
        )
        return str(stdout), str(stderr)


__all__ = ["ModalSdkAdapter"]
