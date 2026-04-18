"""Contract tests for the Modal symbol inventory."""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import os
from collections.abc import AsyncIterable
from datetime import UTC, datetime

import pytest
from google.protobuf.empty_pb2 import Empty
from google.protobuf.timestamp_pb2 import Timestamp

try:
    import modal
    from modal import _logs as modal_logs
    from modal import sandbox as modal_sandbox
    from modal import volume as modal_volume
except ModuleNotFoundError:  # pragma: no cover - dependency drift guard
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise
    pytest.skip("modal package is unavailable", allow_module_level=True)

from modal_mcp.adapters.capabilities import CAPABILITY_REGISTRY, RPC_INVENTORY

try:
    from modal_proto import api_pb2
except ModuleNotFoundError:  # pragma: no cover - dependency drift guard
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise
    pytest.skip("modal_proto package is unavailable", allow_module_level=True)


PINNED_MODAL_VERSION = "1.4.1"


def _is_strict_modal_drift_ci() -> bool:
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    ref = os.environ.get("GITHUB_REF", "")
    nightly = os.environ.get("NIGHTLY", "").strip().lower() in {"1", "true", "yes"}
    if ref_name in {"main", "nightly"}:
        return True
    if ref in {"refs/heads/main", "refs/heads/nightly"}:
        return True
    return nightly


def _maybe_xfail_modal_version_drift() -> None:
    actual_version = getattr(modal, "__version__", None)
    if actual_version == PINNED_MODAL_VERSION:
        return

    message = (
        f"Modal drift detected: installed {actual_version!r}, "
        f"expected {PINNED_MODAL_VERSION!r}"
    )
    if _is_strict_modal_drift_ci():
        pytest.fail(message)
    pytest.xfail(message)


async def _collect_batches(result: AsyncIterable[object]) -> list[object]:
    return [item async for item in result]


class _FakeLogsStub:
    def __init__(self, count_response: object, fetch_response: object) -> None:
        self.count_requests: list[object] = []
        self.fetch_requests: list[object] = []
        self._count_response = count_response
        self._fetch_response = fetch_response

    async def AppCountLogs(self, request: object) -> object:
        self.count_requests.append(request)
        return self._count_response

    async def AppFetchLogs(self, request: object) -> object:
        self.fetch_requests.append(request)
        return self._fetch_response


class _FakeLogsClient:
    def __init__(self, count_response: object, fetch_response: object) -> None:
        self.stub = _FakeLogsStub(count_response, fetch_response)


def test_modal_stub_rpc_inventory_matches_spec() -> None:
    """The verified inventory covers every documented §6.2 surface."""

    expected_methods = {
        "WorkspaceNameLookup",
        "AppList",
        "AppDeploymentHistory",
        "AppFetchLogs",
        "AppGetLogs",
        "AppRollback",
        "AppStop",
        "TaskList",
        "TaskGetInfo",
        "ContainerStop",
        "VolumeList",
        "VolumeListFiles2",
        "VolumeGetFile2",
        "SandboxList",
        "Sandbox.from_id",
    }

    actual_methods = {item.method_name for item in RPC_INVENTORY}
    assert actual_methods == expected_methods
    assert len(RPC_INVENTORY) == len(actual_methods)

    for item in RPC_INVENTORY:
        assert item.request_type
        assert item.response_type
        assert item.mode in {"unary", "unary_stream"}
        assert item.environment_strategy
        assert item.backend
        assert item.source_note

    assert hasattr(modal.Sandbox, "from_id")
    sandbox_from_id_sig = inspect.signature(modal.Sandbox.from_id)
    assert sandbox_from_id_sig.parameters["sandbox_id"].annotation in {
        str,
        "str",
        inspect.Signature.empty,
    }

    for item in RPC_INVENTORY:
        request_type = Empty
        if item.request_type != "Empty":
            request_type = getattr(api_pb2, item.request_type)
        response_type = Empty
        if item.response_type != "Empty":
            response_type = getattr(api_pb2, item.response_type)
        assert request_type is not None
        assert response_type is not None


def test_capability_registry_covers_the_enabled_toolsets() -> None:
    """Capability names match the configured toolset surface."""

    assert set(CAPABILITY_REGISTRY) == {
        "discovery",
        "apps",
        "containers",
        "logs",
        "volumes",
        "sandboxes",
    }

    for capability in CAPABILITY_REGISTRY.values():
        assert capability.name
        assert capability.method_names
        assert capability.rpc_method_names
        assert capability.read_only is True


def test_modal_log_helpers_have_expected_signature() -> None:
    """Log helper signatures and field mappings stay aligned with the contract."""

    assert inspect.isasyncgenfunction(modal_logs.fetch_logs)
    assert inspect.isasyncgenfunction(modal_logs.tail_logs)

    filters_fields = [
        field.name for field in dataclasses.fields(modal_logs.LogsFilters)
    ]
    assert filters_fields == [
        "source",
        "function_id",
        "function_call_id",
        "task_id",
        "sandbox_id",
        "search_text",
    ]
    assert modal_logs.LogsFilters().source == api_pb2.FILE_DESCRIPTOR_UNSPECIFIED

    fetch_sig = inspect.signature(modal_logs.fetch_logs)
    assert list(fetch_sig.parameters) == [
        "client",
        "app_id",
        "since",
        "until",
        "filters",
    ]
    assert fetch_sig.parameters["filters"].kind is inspect.Parameter.KEYWORD_ONLY
    assert fetch_sig.parameters["filters"].default is None

    tail_sig = inspect.signature(modal_logs.tail_logs)
    assert list(tail_sig.parameters) == [
        "client",
        "app_id",
        "n",
        "since",
        "until",
        "filters",
    ]
    assert tail_sig.parameters["n"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert tail_sig.parameters["since"].kind is inspect.Parameter.KEYWORD_ONLY
    assert tail_sig.parameters["until"].kind is inspect.Parameter.KEYWORD_ONLY
    assert tail_sig.parameters["filters"].kind is inspect.Parameter.KEYWORD_ONLY
    assert tail_sig.parameters["since"].default is None
    assert tail_sig.parameters["until"].default is None
    assert tail_sig.parameters["filters"].default is None

    for helper, expected_rpc_names in (
        (modal_logs.fetch_logs, ("AppCountLogs",)),
        (modal_logs.tail_logs, ("AppFetchLogs",)),
    ):
        source = inspect.getsource(helper)
        for field_name in filters_fields:
            assert f"filters.{field_name}" in source
        for rpc_name in expected_rpc_names:
            assert rpc_name in source

    since = datetime(2026, 1, 1, tzinfo=UTC)
    until = datetime(2026, 1, 2, tzinfo=UTC)
    filters = modal_logs.LogsFilters(
        source=api_pb2.FILE_DESCRIPTOR_STDOUT,
        function_id="function-id",
        function_call_id="function-call-id",
        task_id="task-id",
        sandbox_id="sandbox-id",
        search_text="needle",
    )

    bucket_start_at = Timestamp()
    bucket_start_at.FromDatetime(since)
    count_response = api_pb2.AppCountLogsResponse()
    count_bucket = count_response.buckets.add()
    count_bucket.bucket_start_at.CopyFrom(bucket_start_at)
    count_bucket.stdout_logs = 1
    count_bucket.stderr_logs = 0
    count_bucket.system_logs = 0

    fetch_response = api_pb2.AppFetchLogsResponse()
    fetch_batch = fetch_response.batches.add()
    client = _FakeLogsClient(count_response, fetch_response)

    batches = asyncio.run(
        _collect_batches(
            modal_logs.fetch_logs(
                client,
                "app-id",
                since,
                until,
                filters=filters,
            )
        )
    )
    assert batches == [fetch_batch]

    assert len(client.stub.count_requests) == 1
    count_request = client.stub.count_requests[0]
    assert count_request.app_id == "app-id"
    assert count_request.source == filters.source
    assert count_request.function_id == filters.function_id
    assert count_request.function_call_id == filters.function_call_id
    assert count_request.task_id == filters.task_id
    assert count_request.sandbox_id == filters.sandbox_id
    assert count_request.search_text == filters.search_text

    assert len(client.stub.fetch_requests) == 1
    fetch_request = client.stub.fetch_requests[0]
    assert fetch_request.app_id == "app-id"
    assert fetch_request.since.seconds >= int(since.timestamp())
    assert fetch_request.until.seconds <= int(until.timestamp())
    assert fetch_request.limit > 0
    assert fetch_request.source == filters.source
    assert fetch_request.function_id == filters.function_id
    assert fetch_request.function_call_id == filters.function_call_id
    assert fetch_request.task_id == filters.task_id
    assert fetch_request.sandbox_id == filters.sandbox_id
    assert fetch_request.search_text == filters.search_text

    tail_response = api_pb2.AppFetchLogsResponse()
    tail_batch = tail_response.batches.add()
    tail_client = _FakeLogsClient(None, tail_response)
    tail_batches = asyncio.run(
        _collect_batches(
            modal_logs.tail_logs(
                tail_client,
                "app-id",
                1,
                since=since,
                until=until,
                filters=filters,
            )
        )
    )
    assert tail_batches == [tail_batch]

    assert len(tail_client.stub.fetch_requests) == 1
    tail_request = tail_client.stub.fetch_requests[0]
    assert tail_request.app_id == "app-id"
    assert tail_request.limit == 1
    assert tail_request.since.seconds == int(since.timestamp())
    assert tail_request.until.seconds == int(until.timestamp())
    assert tail_request.source == filters.source
    assert tail_request.function_id == filters.function_id
    assert tail_request.function_call_id == filters.function_call_id
    assert tail_request.task_id == filters.task_id
    assert tail_request.sandbox_id == filters.sandbox_id
    assert tail_request.search_text == filters.search_text


def test_volume_v2_file_methods_exist() -> None:
    """Volume v2 file helpers remain wired to the v2 RPC paths."""

    assert hasattr(modal.Volume, "listdir")
    assert hasattr(modal.Volume, "read_file")
    assert hasattr(modal.Volume, "from_id")

    listdir_sig = inspect.signature(modal.Volume.listdir)
    read_file_sig = inspect.signature(modal.Volume.read_file)
    from_id_sig = inspect.signature(modal.Volume.from_id)

    assert list(listdir_sig.parameters) == ["self", "path", "recursive"]
    assert listdir_sig.parameters["recursive"].kind is inspect.Parameter.KEYWORD_ONLY
    assert listdir_sig.parameters["recursive"].default is False

    assert list(read_file_sig.parameters) == ["self", "path"]
    assert read_file_sig.parameters["path"].annotation in {str, "str"}

    assert list(from_id_sig.parameters) == ["volume_id", "client"]
    assert from_id_sig.parameters["client"].default is None

    iterdir_source = inspect.getsource(modal_volume._Volume.iterdir)
    read_file_source = inspect.getsource(modal_volume._Volume.read_file)
    assert "VolumeListFiles2Request" in iterdir_source
    assert "VolumeListFiles2" in iterdir_source
    assert "VolumeGetFile2Request" in read_file_source
    assert "VolumeGetFile2" in read_file_source

    assert modal.Volume.listdir is not None
    assert modal.Volume.read_file is not None
    assert modal.Volume.from_id is not None

    sandbox_from_id_sig = inspect.signature(modal_sandbox._Sandbox.from_id)
    assert sandbox_from_id_sig.parameters["sandbox_id"].annotation in {str, "str"}


def test_modal_latest_version_drift_is_non_blocking() -> None:
    """Version drift is only fatal in strict main/nightly CI contexts."""

    _maybe_xfail_modal_version_drift()
    assert getattr(modal, "__version__", None) == PINNED_MODAL_VERSION
