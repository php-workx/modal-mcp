"""Contract tests for the adapter symbol inventory."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from google.protobuf.empty_pb2 import Empty

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from modal_mcp.adapters.capabilities import CAPABILITY_REGISTRY, RPC_INVENTORY

api_pb2: Any | None = None
try:
    from modal_proto import api_pb2 as api_pb2_module
except ModuleNotFoundError:  # pragma: no cover - dependency drift guard
    pass
else:
    api_pb2 = api_pb2_module


def test_rpc_inventory_is_complete_and_well_shaped() -> None:
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

    if api_pb2 is None:
        return

    for item in RPC_INVENTORY:
        if item.request_type == "Empty":
            request_type = Empty
        else:
            request_type = getattr(api_pb2, item.request_type)
        if item.response_type == "Empty":
            response_type = Empty
        else:
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
