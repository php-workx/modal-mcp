"""Modal SDK-backed adapter implementation."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from modal_mcp.adapters.credentials import ModalCredentials
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
    AppNormalizer,
    ContainerNormalizer,
    DeploymentNormalizer,
    EnvironmentNormalizer,
    LogBatchNormalizer,
    SandboxNormalizer,
    VolumeNormalizer,
    WorkspaceNormalizer,
)
from modal_mcp.domain.refs import RefCodec

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

    @classmethod
    async def from_credentials(
        cls,
        creds: ModalCredentials,
        *,
        client_factory: ClientFactory | None = None,
    ) -> ModalRpcClient:
        """Construct a ModalRpcClient from resolved credentials.

        Instantiates the Modal SDK client via ``modal.Client.from_credentials.aio``
        and runs a cheap ``WorkspaceNameLookup`` auth probe before returning.
        Auth failures surface as :class:`ModalAdapterError(UPSTREAM_ERROR)`.
        """
        try:
            import modal
        except ImportError as exc:  # pragma: no cover - dependency guard
            msg = "Modal SDK is not installed"
            raise ModalAdapterError(ErrorCode.INTERNAL_DRIFT, msg) from exc

        client = await modal.Client.from_credentials.aio(
            creds.token_id.get_secret_value(),
            creds.token_secret.get_secret_value(),
        )
        rpc = cls(client, client_factory=client_factory)
        try:
            rpc.call("WorkspaceNameLookup", rpc.request("Empty"))
        except ModalAdapterError:
            raise
        except Exception as exc:
            msg = (
                f"Modal auth probe failed for credentials ({creds.describe()}): "
                f"{type(exc).__name__}"
            )
            raise ModalAdapterError(
                ErrorCode.UPSTREAM_ERROR, msg, retryable=False
            ) from exc
        return rpc

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

    @property
    def _stub(self) -> Any:
        return getattr(self._client, "stub", self._client)

    def _call_rpc(self, method_name: str, request: Any | None = None) -> Any:
        method = getattr(self._stub, method_name)
        if request is None:
            return method()
        return method(request)


class ModalSdkAdapter:
    """Read-only Modal adapter backed by an injected or real Modal client."""

    def __init__(
        self,
        settings: Settings,
        rpc: ModalRpcClient,
        ref_codec: RefCodec,
    ) -> None:
        self._settings = settings
        self._rpc = rpc
        self._ref_codec = ref_codec
        keys = self._signing_keys
        self._workspace_normalizer = WorkspaceNormalizer(signing_keys=keys)
        self._environment_normalizer = EnvironmentNormalizer(signing_keys=keys)
        self._app_normalizer = AppNormalizer(signing_keys=keys)
        self._container_normalizer = ContainerNormalizer(signing_keys=keys)
        self._volume_normalizer = VolumeNormalizer(signing_keys=keys)
        self._sandbox_normalizer = SandboxNormalizer(signing_keys=keys)
        self._deployment_normalizer = DeploymentNormalizer(signing_keys=keys)
        self._log_normalizer = LogBatchNormalizer(signing_keys=keys)

    @property
    def _signing_keys(self) -> tuple[tuple[str, bytes], ...]:
        """Bridge: expose raw key tuples for callers that predate RefCodec."""
        return self._ref_codec.signing_key_pairs()

    @classmethod
    async def create(
        cls,
        settings: Settings,
        *,
        client: Any,
        ref_codec: RefCodec,
    ) -> ModalSdkAdapter:
        """Pure assembly: wrap client in ModalRpcClient, store codec, build normalizers.

        Credential resolution and client construction are *not* this class's
        responsibility — they happen in ``CredentialSource.resolve`` and
        ``ModalRpcClient.from_credentials``, orchestrated from
        ``server._default_adapter_factory`` (or a test bootstrapper).
        """
        rpc = client if isinstance(client, ModalRpcClient) else ModalRpcClient(client)
        return cls(settings, rpc, ref_codec)

    async def aclose(self) -> None:
        """Close the underlying Modal client via the RPC transport layer."""
        await self._rpc.aclose()

    def _environment_name(self, environment_name: str | None = None) -> str | None:
        return environment_name or self._settings.modal_environment

    def _verify_ref_env(self, ref: str, expected_env: str | None = None) -> str:
        """Decode a public ref and enforce the requested environment."""

        env = expected_env or self._settings.modal_environment
        payload = self._ref_codec.decode(ref, expected_env=env)
        return payload.id

    def _native_id(self, value: str, expected_env: str | None = None) -> str:
        if value.startswith("mref1."):
            return self._verify_ref_env(value, expected_env=expected_env)
        return value

    def validate_auth(self) -> None:
        """Verify that the current client can perform a cheap workspace lookup."""

        self._rpc.call("WorkspaceNameLookup", self._rpc.request("Empty"))

    def whoami(self) -> Workspace:
        """Return the authenticated workspace summary."""

        raw = self._rpc.call("WorkspaceNameLookup", self._rpc.request("Empty"))
        entity, warnings = self._workspace_normalizer.normalize(raw)
        if entity is None:
            reason = "; ".join(warnings) or "workspace normalization failed"
            raise ModalAdapterError(
                ErrorCode.UPSTREAM_ERROR,
                f"failed to normalize workspace: {reason}",
            )
        return entity

    def list_workspaces(self) -> Sequence[Workspace]:
        """Return local/current workspace information."""

        return [self.whoami()]

    def list_environments(self) -> tuple[Sequence[Environment], list[str]]:
        """Return environments visible to the authenticated workspace."""

        raw = self._rpc.call("EnvironmentList", self._rpc.request("Empty"))
        results, warnings = [], []
        for item in _items(raw, "items", "environments"):
            entity, w = self._environment_normalizer.normalize(item)
            warnings.extend(w)
            if entity is not None:
                results.append(entity)
        return results, warnings

    def get_environment(self, environment_name: str) -> Environment | None:
        """Return a single environment by name."""

        environments, _ = self.list_environments()
        for environment in environments:
            if environment.name == environment_name:
                return environment
        return None

    def list_apps(
        self, environment_name: str | None = None
    ) -> tuple[Sequence[App], list[str]]:
        """Return apps visible in an environment."""

        env = self._environment_name(environment_name)
        request = self._rpc.request("AppListRequest", environment_name=env)
        raw = self._rpc.call("AppList", request)
        results, warnings = [], []
        for item in _items(raw, "apps", "items"):
            entity, w = self._app_normalizer.normalize(item)
            warnings.extend(w)
            if entity is not None:
                results.append(entity)
        return results, warnings

    def get_app(self, app_id: str, environment_name: str | None = None) -> App | None:
        """Return a single app by public ref, id, or name."""

        native_id = self._native_id(
            app_id,
            expected_env=self._environment_name(environment_name),
        )
        apps, _ = self.list_apps(environment_name)
        for app in apps:
            if app.app_ref == app_id or app.name == app_id:
                return app
            try:
                app_native_id = self._ref_codec.decode(
                    app.app_ref,
                    expected_env=self._environment_name(environment_name),
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
        request = self._rpc.request("AppDeploymentHistoryRequest", app_id=native_id)
        raw = self._rpc.call("AppDeploymentHistory", request)
        results = []
        for item in _items(raw, "app_deployment_histories", "items"):
            entity, _ = self._deployment_normalizer.normalize(item)
            if entity is not None:
                results.append(entity)
        return results

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
        entity, _ = self._log_normalizer.normalize(raw)
        if entity is None:
            msg = "log batch normalization failed unexpectedly"
            raise ModalAdapterError(ErrorCode.UPSTREAM_ERROR, msg)
        return entity

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
    ) -> tuple[Sequence[Container], list[str]]:
        """Return containers for an environment or app."""

        env = self._environment_name(environment_name)
        native_app_id = self._native_id(app_id, expected_env=env) if app_id else None
        request = self._rpc.request(
            "TaskListRequest",
            environment_name=env,
            app_id=native_app_id,
        )
        raw = self._rpc.call("TaskList", request)
        results, warnings = [], []
        for item in _items(raw, "tasks", "items"):
            entity, w = self._container_normalizer.normalize(item)
            warnings.extend(w)
            if entity is not None:
                results.append(entity)
        return results, warnings

    def get_container(self, task_id: str) -> Container | None:
        """Return a single container by task id."""

        native_task_id = self._native_id(task_id)
        request = self._rpc.request("TaskGetInfoRequest", task_id=native_task_id)
        raw = self._rpc.call("TaskGetInfo", request)
        items = _items(raw, "tasks", "items")
        target = items[0] if items else raw
        if not target:
            return None
        entity, _ = self._container_normalizer.normalize(
            target, hint_task_id=native_task_id
        )
        return entity

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
        request = self._rpc.request("VolumeListRequest", environment_name=env)
        raw = self._rpc.call("VolumeList", request)
        results = []
        for item in _items(raw, "items", "volumes"):
            entity, _ = self._volume_normalizer.normalize(item)
            if entity is not None:
                results.append(entity)
        return results

    def ls_volume(
        self,
        volume_id: str,
        path: str = "/",
        *,
        recursive: bool = False,
        max_entries: int | None = None,
    ) -> Sequence[VolumeEntry]:
        """Return volume entries under a path."""

        request = self._rpc.request(
            "VolumeListFiles2Request",
            volume_id=self._native_id(volume_id),
            path=path,
            recursive=recursive,
            max_entries=max_entries,
        )
        raw = self._rpc.call("VolumeListFiles2", request)
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

        request = self._rpc.request(
            "VolumeGetFile2Request",
            volume_id=self._native_id(volume_id),
            path=path,
        )
        raw = self._rpc.call("VolumeGetFile2", request)
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
        request = self._rpc.request(
            "SandboxListRequest",
            environment_name=env,
            app_id=self._native_id(app_id, expected_env=env) if app_id else None,
            tags=dict(tags or {}),
            include_finished=include_finished,
        )
        raw = self._rpc.call("SandboxList", request)
        results = []
        for item in _items(raw, "sandboxes", "items"):
            entity, _ = self._sandbox_normalizer.normalize(item)
            if entity is not None:
                results.append(entity)
        return results

    def get_sandbox(self, sandbox_id: str) -> SandboxSummary | None:
        """Return a single sandbox by id."""

        request = self._rpc.request(
            "SandboxWaitRequest",
            sandbox_id=self._native_id(sandbox_id),
        )
        raw = self._rpc.call("SandboxWait", request)
        entity, _ = self._sandbox_normalizer.normalize(raw)
        return entity

    def get_sandbox_stdio(self, sandbox_id: str) -> tuple[str, str]:
        """Return buffered stdout/stderr text for a sandbox."""

        request = self._rpc.request(
            "SandboxGetLogsRequest",
            sandbox_id=self._native_id(sandbox_id),
        )
        raw = self._rpc.call("SandboxGetLogs", request)
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


__all__ = ["ModalRpcClient", "ModalSdkAdapter"]
