"""Pydantic domain models for Modal MCP responses."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

Ref = str
Cursor = str

T = TypeVar("T")


class _DomainModel(BaseModel):
    """Base model for public domain objects."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class CommitInfo(_DomainModel):
    """Version-control metadata attached to a deployment."""

    vcs: str
    branch: str
    commit_hash: str
    commit_timestamp: datetime
    dirty: bool
    author_name: str | None = None
    author_email: str | None = None
    repo_url: str | None = None


class Workspace(_DomainModel):
    """Workspace summary returned by the adapter."""

    workspace_ref: Ref
    name: str
    source: Literal["local_profile", "authenticated_token"]
    current: bool


class Environment(_DomainModel):
    """Environment summary returned by the adapter."""

    environment_ref: Ref
    name: str
    is_default: bool
    created_at: datetime | None = None
    web_suffix: str | None = None


class App(_DomainModel):
    """Application summary returned by the adapter."""

    app_ref: Ref
    name: str
    description: str
    state: str
    created_at: datetime
    stopped_at: datetime | None = None
    n_running_tasks: int
    environment_ref: Ref


class Deployment(_DomainModel):
    """Normalized deployment history entry."""

    version: int
    status: Literal["active", "superseded", "rolled_back", "unknown"]
    deployed_at: datetime
    client_version: str
    deployed_by: str
    deployed_by_avatar_url: str | None = None
    tag: str | None = None
    rollback_version: int | None = None
    rollback_allowed: bool
    commit: CommitInfo | None = None
    image_digest: str | None = None


class Container(_DomainModel):
    """Task container summary returned by the adapter."""

    container_ref: Ref
    task_id: str
    app_ref: Ref | None = None
    function_id: str | None = None
    function_call_id: str | None = None
    state: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    region: str | None = None


class VolumeSummary(_DomainModel):
    """Volume summary returned by the adapter."""

    volume_ref: Ref
    name: str
    created_at: datetime
    created_by: str | None = None
    environment_ref: Ref


class VolumeEntry(_DomainModel):
    """Entry returned from a volume listing."""

    path: str
    type: Literal["file", "dir", "symlink", "fifo", "socket", "unknown"]
    mtime: datetime
    size_bytes: int


class SandboxSummary(_DomainModel):
    """Sandbox summary returned by the adapter."""

    sandbox_ref: Ref
    sandbox_id: str
    app_ref: Ref | None = None
    name: str | None = None
    created_at: datetime
    status: str
    returncode: int | None = None
    tags: list[str]


class LogEntry(_DomainModel):
    """Single log record returned by the adapter."""

    ts: datetime | None = None
    source: str
    message: str
    app_ref: Ref | None = None
    container_ref: Ref | None = None
    function_id: str | None = None
    function_call_id: str | None = None
    sandbox_ref: Ref | None = None
    dedup_key: str | None = None


class LogSummary(_DomainModel):
    """Summary metadata for a log response."""

    error_signatures: list[str]
    top_sources: list[str]
    total_entries: int
    truncated: bool
    deduped_count: int


class Page(_DomainModel, Generic[T]):  # noqa: UP046
    """Generic page of items with cursor pagination metadata."""

    items: list[T]
    next_cursor: Cursor | None = None
    truncated: bool


class LogsPage(_DomainModel):
    """Paged log response with summary metadata."""

    entries: list[LogEntry]
    summary: LogSummary
    next_cursor: Cursor | None = None
    stream_reset: bool = False


__all__ = [
    "App",
    "CommitInfo",
    "Container",
    "Cursor",
    "Deployment",
    "Environment",
    "LogEntry",
    "LogSummary",
    "LogsPage",
    "Page",
    "Ref",
    "SandboxSummary",
    "VolumeEntry",
    "VolumeSummary",
    "Workspace",
]
