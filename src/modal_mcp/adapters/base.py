"""Adapter protocol for Modal-backed read operations."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from modal_mcp.domain.models import (
    App,
    Container,
    Deployment,
    Environment,
    LogEntry,
    LogsPage,
    SandboxSummary,
    VolumeEntry,
    VolumeSummary,
    Workspace,
)


@runtime_checkable
class ModalAdapter(Protocol):
    """Read-only surface for Modal data access."""

    def validate_auth(self) -> None:
        """Verify that the bound Modal credentials can be used."""

    def whoami(self) -> Workspace:
        """Return the active workspace for the current credentials."""

    def list_workspaces(self) -> Sequence[Workspace]:
        """Return every workspace visible to the current credentials."""

    def list_environments(self) -> Sequence[Environment]:
        """Return the environments visible in the current workspace."""

    def get_environment(self, environment_name: str) -> Environment | None:
        """Return a single environment by name when it exists."""

    def list_apps(self, environment_name: str | None = None) -> Sequence[App]:
        """Return the apps visible in an environment."""

    def get_app(self, app_id: str, environment_name: str | None = None) -> App | None:
        """Return a single app by id when it exists."""

    def list_app_deployments(
        self,
        app_id: str,
        environment_name: str | None = None,
    ) -> Sequence[Deployment]:
        """Return deployment history for an app."""

    def get_app_logs(
        self,
        app_id: str,
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
        """Return a bounded app log window and summary."""

    def tail_app_logs(
        self,
        app_id: str,
        *,
        timeout: float = 30.0,
        source: str | None = None,
        function_id: str | None = None,
        function_call_id: str | None = None,
        task_id: str | None = None,
        sandbox_id: str | None = None,
        search_text: str | None = None,
    ) -> AsyncIterator[LogEntry]:
        """Stream app logs until the backend times out."""

    def list_containers(
        self,
        environment_name: str | None = None,
        app_id: str | None = None,
    ) -> Sequence[Container]:
        """Return the containers visible for an environment or app."""

    def get_container(self, task_id: str) -> Container | None:
        """Return a single container by task id when it exists."""

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
        """Return a bounded log window for a single container."""

    def list_volumes(
        self, environment_name: str | None = None
    ) -> Sequence[VolumeSummary]:
        """Return the volumes visible in an environment."""

    def ls_volume(
        self,
        volume_id: str,
        path: str = "/",
        *,
        recursive: bool = False,
        max_entries: int | None = None,
    ) -> Sequence[VolumeEntry]:
        """Return the volume entries under a path."""

    def read_volume_text(
        self,
        volume_id: str,
        path: str,
        *,
        encoding: str = "utf-8",
    ) -> str:
        """Return a text file from a volume."""

    def stat_volume_path(self, volume_id: str, path: str) -> VolumeEntry | None:
        """Return metadata for a single volume path."""

    def list_sandboxes(
        self,
        environment_name: str | None = None,
        app_id: str | None = None,
        tags: Mapping[str, str] | None = None,
        include_finished: bool = False,
    ) -> Sequence[SandboxSummary]:
        """Return the sandboxes visible in an environment or app."""

    def get_sandbox(self, sandbox_id: str) -> SandboxSummary | None:
        """Return a single sandbox by id when it exists."""

    def get_sandbox_stdio(self, sandbox_id: str) -> tuple[str, str]:
        """Return the combined stdout and stderr text for a sandbox."""


__all__ = ["ModalAdapter"]
