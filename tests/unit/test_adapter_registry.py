"""Unit tests for the process-wide Modal adapter registry."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from modal_mcp.adapters.base import ModalAdapter
from modal_mcp.adapters.registry import bind_modal_adapter, get_modal_adapter


@pytest.fixture(autouse=True)
def clear_adapter_binding() -> None:
    """Keep registry tests isolated from one another."""

    bind_modal_adapter(None)
    yield
    bind_modal_adapter(None)


class _DummyAdapter:
    """Minimal object that satisfies the adapter protocol at runtime."""

    def validate_auth(self) -> None:
        return None

    def whoami(self) -> Any:
        return None

    def list_workspaces(self) -> list[Any]:
        return []

    def list_environments(self) -> list[Any]:
        return []

    def get_environment(self, environment_name: str) -> Any:
        return None

    def list_apps(self, environment_name: str | None = None) -> list[Any]:
        return []

    def get_app(self, app_id: str, environment_name: str | None = None) -> Any:
        return None

    def list_app_deployments(
        self, app_id: str, environment_name: str | None = None
    ) -> list[Any]:
        return []

    def get_app_logs(self, app_id: str, **kwargs: Any) -> Any:
        return None

    async def tail_app_logs(self, app_id: str, **kwargs: Any) -> AsyncIterator[Any]:
        if False:  # pragma: no cover - keep the return type as an async iterator.
            yield None

    def list_containers(
        self, environment_name: str | None = None, app_id: str | None = None
    ) -> list[Any]:
        return []

    def get_container(self, task_id: str) -> Any:
        return None

    def get_container_logs(self, task_id: str, **kwargs: Any) -> Any:
        return None

    def list_volumes(self, environment_name: str | None = None) -> list[Any]:
        return []

    def ls_volume(
        self,
        volume_id: str,
        path: str = "/",
        *,
        recursive: bool = False,
        max_entries: int | None = None,
    ) -> list[Any]:
        return []

    def read_volume_text(
        self, volume_id: str, path: str, *, encoding: str = "utf-8"
    ) -> str:
        return ""

    def stat_volume_path(self, volume_id: str, path: str) -> Any:
        return None

    def list_sandboxes(
        self,
        environment_name: str | None = None,
        app_id: str | None = None,
        tags: Mapping[str, str] | None = None,
        include_finished: bool = False,
    ) -> list[Any]:
        return []

    def get_sandbox(self, sandbox_id: str) -> Any:
        return None

    def get_sandbox_stdio(self, sandbox_id: str) -> tuple[str, str]:
        return ("", "")


def test_get_modal_adapter_requires_a_binding() -> None:
    """The registry fails cleanly before anything is bound."""

    with pytest.raises(LookupError, match="no Modal adapter has been bound"):
        get_modal_adapter()


def test_bind_modal_adapter_returns_the_same_object() -> None:
    """Binding replaces the process-wide adapter reference."""

    adapter = _DummyAdapter()
    bind_modal_adapter(adapter)

    bound = get_modal_adapter()

    assert bound is adapter
    assert isinstance(bound, ModalAdapter)


def test_bind_modal_adapter_can_clear_the_binding() -> None:
    """Binding None clears the process-wide adapter reference."""

    bind_modal_adapter(_DummyAdapter())
    bind_modal_adapter(None)

    with pytest.raises(LookupError, match="no Modal adapter has been bound"):
        get_modal_adapter()
