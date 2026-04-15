"""Capability and RPC inventory definitions for Modal adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RpcSpec:
    """Metadata for a verified Modal RPC or helper surface."""

    method_name: str
    request_type: str
    response_type: str
    mode: str
    environment_strategy: str
    backend: str
    source_note: str


RPC_INVENTORY: tuple[RpcSpec, ...] = (
    RpcSpec(
        method_name="WorkspaceNameLookup",
        request_type="Empty",
        response_type="WorkspaceNameLookupResponse",
        mode="unary",
        environment_strategy="workspace_implicit",
        backend="grpc",
        source_note="modal_proto.api_pb2.WorkspaceNameLookup",
    ),
    RpcSpec(
        method_name="AppList",
        request_type="AppListRequest",
        response_type="AppListResponse",
        mode="unary",
        environment_strategy="explicit_environment_name",
        backend="grpc",
        source_note="modal.client.stub.AppList",
    ),
    RpcSpec(
        method_name="AppDeploymentHistory",
        request_type="AppDeploymentHistoryRequest",
        response_type="AppDeploymentHistoryResponse",
        mode="unary",
        environment_strategy="app_id",
        backend="grpc",
        source_note="modal.client.stub.AppDeploymentHistory",
    ),
    RpcSpec(
        method_name="AppFetchLogs",
        request_type="AppFetchLogsRequest",
        response_type="AppFetchLogsResponse",
        mode="unary",
        environment_strategy="app_id",
        backend="grpc",
        source_note="modal._logs.fetch_logs",
    ),
    RpcSpec(
        method_name="AppGetLogs",
        request_type="AppGetLogsRequest",
        response_type="TaskLogsBatch",
        mode="unary_stream",
        environment_strategy="app_id",
        backend="grpc",
        source_note="modal.app._App.get_logs",
    ),
    RpcSpec(
        method_name="AppRollback",
        request_type="AppRollbackRequest",
        response_type="Empty",
        mode="unary",
        environment_strategy="app_id",
        backend="grpc",
        source_note="modal.client.stub.AppRollback",
    ),
    RpcSpec(
        method_name="AppStop",
        request_type="AppStopRequest",
        response_type="Empty",
        mode="unary",
        environment_strategy="app_id",
        backend="grpc",
        source_note="modal.client.stub.AppStop",
    ),
    RpcSpec(
        method_name="TaskList",
        request_type="TaskListRequest",
        response_type="TaskListResponse",
        mode="unary",
        environment_strategy="environment_name_or_app_id",
        backend="grpc",
        source_note="modal.runner.task listing",
    ),
    RpcSpec(
        method_name="TaskGetInfo",
        request_type="TaskGetInfoRequest",
        response_type="TaskGetInfoResponse",
        mode="unary",
        environment_strategy="task_id",
        backend="grpc",
        source_note="modal.client.stub.TaskGetInfo",
    ),
    RpcSpec(
        method_name="ContainerStop",
        request_type="ContainerStopRequest",
        response_type="ContainerStopResponse",
        mode="unary",
        environment_strategy="task_id",
        backend="grpc",
        source_note="modal.runner.ContainerStop",
    ),
    RpcSpec(
        method_name="VolumeList",
        request_type="VolumeListRequest",
        response_type="VolumeListResponse",
        mode="unary",
        environment_strategy="environment_name",
        backend="grpc",
        source_note="modal.volume.Volume.objects.list",
    ),
    RpcSpec(
        method_name="VolumeListFiles2",
        request_type="VolumeListFiles2Request",
        response_type="VolumeListFiles2Response",
        mode="unary_stream",
        environment_strategy="volume_id",
        backend="grpc",
        source_note="modal.volume._Volume.listdir",
    ),
    RpcSpec(
        method_name="VolumeGetFile2",
        request_type="VolumeGetFile2Request",
        response_type="VolumeGetFile2Response",
        mode="unary",
        environment_strategy="volume_id",
        backend="grpc",
        source_note="modal.volume._Volume.read_file",
    ),
    RpcSpec(
        method_name="SandboxList",
        request_type="SandboxListRequest",
        response_type="SandboxListResponse",
        mode="unary",
        environment_strategy="environment_name_or_app_id",
        backend="grpc",
        source_note="modal.sandbox._Sandbox.list",
    ),
    RpcSpec(
        method_name="Sandbox.from_id",
        request_type="SandboxWaitRequest",
        response_type="SandboxWaitResponse",
        mode="unary",
        environment_strategy="sandbox_id",
        backend="sdk_helper",
        source_note="modal.sandbox._Sandbox.from_id",
    ),
)


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """Named capability bundle exposed by the adapter."""

    name: str
    method_names: tuple[str, ...]
    rpc_method_names: tuple[str, ...]
    read_only: bool = True


CAPABILITY_REGISTRY: dict[str, CapabilitySpec] = {
    "discovery": CapabilitySpec(
        name="discovery",
        method_names=(
            "validate_auth",
            "whoami",
            "list_workspaces",
            "list_environments",
            "get_environment",
        ),
        rpc_method_names=("WorkspaceNameLookup",),
    ),
    "apps": CapabilitySpec(
        name="apps",
        method_names=("list_apps", "get_app", "list_app_deployments"),
        rpc_method_names=("AppList", "AppDeploymentHistory", "AppRollback", "AppStop"),
    ),
    "containers": CapabilitySpec(
        name="containers",
        method_names=("list_containers", "get_container", "get_container_logs"),
        rpc_method_names=("TaskList", "TaskGetInfo", "ContainerStop"),
    ),
    "logs": CapabilitySpec(
        name="logs",
        method_names=("get_app_logs", "tail_app_logs"),
        rpc_method_names=("AppFetchLogs", "AppGetLogs"),
    ),
    "volumes": CapabilitySpec(
        name="volumes",
        method_names=(
            "list_volumes",
            "ls_volume",
            "read_volume_text",
            "stat_volume_path",
        ),
        rpc_method_names=("VolumeList", "VolumeListFiles2", "VolumeGetFile2"),
    ),
    "sandboxes": CapabilitySpec(
        name="sandboxes",
        method_names=("list_sandboxes", "get_sandbox", "get_sandbox_stdio"),
        rpc_method_names=("SandboxList", "Sandbox.from_id"),
    ),
}


__all__ = [
    "CAPABILITY_REGISTRY",
    "RPC_INVENTORY",
    "CapabilitySpec",
    "RpcSpec",
]
