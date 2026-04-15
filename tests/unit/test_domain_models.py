# ruff: noqa: I001
"""Unit tests for the Modal MCP domain models."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from modal_mcp.domain import (
    App,
    CommitInfo,
    Container,
    Deployment,
    Environment,
    LogEntry,
    LogSummary,
    LogsPage,
    Page,
    SandboxSummary,
    VolumeEntry,
    VolumeSummary,
    Workspace,
)


def test_domain_models_serialize_public_fields() -> None:
    """Models dump stable public field names and JSON-friendly values."""

    commit = CommitInfo(
        vcs="git",
        branch="main",
        commit_hash="abc123",
        commit_timestamp=datetime(2026, 4, 15, 10, 0, 0),
        dirty=False,
        author_name="Ada",
        author_email="ada@example.com",
        repo_url="https://example.com/repo",
    )
    workspace = Workspace(
        workspace_ref="mref1.workspace.sig",
        name="Acme",
        source="authenticated_token",
        current=True,
    )
    environment = Environment(
        environment_ref="mref1.environment.sig",
        name="prod",
        is_default=True,
        created_at=datetime(2026, 4, 15, 10, 1, 0),
        web_suffix="acme.modal.run",
    )
    app = App(
        app_ref="mref1.app.sig",
        name="api",
        description="Primary API",
        state="running",
        created_at=datetime(2026, 4, 15, 10, 2, 0),
        stopped_at=None,
        n_running_tasks=3,
        environment_ref=environment.environment_ref,
    )
    deployment = Deployment(
        version=7,
        status="active",
        deployed_at=datetime(2026, 4, 15, 10, 3, 0),
        client_version="1.4.1",
        deployed_by="ronny",
        deployed_by_avatar_url=None,
        tag="release-7",
        rollback_version=6,
        rollback_allowed=True,
        commit=commit,
        image_digest="sha256:deadbeef",
    )
    container = Container(
        container_ref="mref1.container.sig",
        task_id="task_123",
        app_ref=app.app_ref,
        function_id="fn_456",
        function_call_id=None,
        state="running",
        started_at=datetime(2026, 4, 15, 10, 4, 0),
        finished_at=None,
        region="us-east-1",
    )
    volume_summary = VolumeSummary(
        volume_ref="mref1.volume.sig",
        name="uploads",
        created_at=datetime(2026, 4, 15, 10, 5, 0),
        created_by="ronny",
        environment_ref=environment.environment_ref,
    )
    volume_entry = VolumeEntry(
        path="/data/report.txt",
        type="file",
        mtime=datetime(2026, 4, 15, 10, 6, 0),
        size_bytes=512,
    )
    sandbox = SandboxSummary(
        sandbox_ref="mref1.sandbox.sig",
        sandbox_id="sb_123",
        app_ref=app.app_ref,
        name="job-runner",
        created_at=datetime(2026, 4, 15, 10, 7, 0),
        status="finished",
        returncode=0,
        tags=["ci", "nightly"],
    )
    log_entry = LogEntry(
        ts=datetime(2026, 4, 15, 10, 8, 0),
        source="app",
        message="started",
        app_ref=app.app_ref,
        container_ref=container.container_ref,
        function_id=container.function_id,
        function_call_id=None,
        sandbox_ref=sandbox.sandbox_ref,
        dedup_key="dedup-1",
    )
    log_summary = LogSummary(
        error_signatures=["timeout"],
        top_sources=["app"],
        total_entries=1,
        truncated=False,
        deduped_count=0,
    )

    page = Page[Workspace](
        items=[workspace],
        next_cursor="mc1.cursor.sig",
        truncated=False,
    )
    logs_page = LogsPage(
        entries=[log_entry],
        summary=log_summary,
        next_cursor=None,
    )

    assert workspace.model_dump(mode="json") == {
        "workspace_ref": "mref1.workspace.sig",
        "name": "Acme",
        "source": "authenticated_token",
        "current": True,
    }
    assert environment.model_dump(mode="json")["created_at"] == "2026-04-15T10:01:00"
    assert app.model_dump(mode="json")["environment_ref"] == environment.environment_ref
    assert deployment.model_dump(mode="json") == {
        "version": 7,
        "status": "active",
        "deployed_at": "2026-04-15T10:03:00",
        "client_version": "1.4.1",
        "deployed_by": "ronny",
        "deployed_by_avatar_url": None,
        "tag": "release-7",
        "rollback_version": 6,
        "rollback_allowed": True,
        "commit": {
            "vcs": "git",
            "branch": "main",
            "commit_hash": "abc123",
            "commit_timestamp": "2026-04-15T10:00:00",
            "dirty": False,
            "author_name": "Ada",
            "author_email": "ada@example.com",
            "repo_url": "https://example.com/repo",
        },
        "image_digest": "sha256:deadbeef",
    }
    assert container.model_dump(mode="json")["app_ref"] == app.app_ref
    assert (
        volume_summary.model_dump(mode="json")["environment_ref"]
        == environment.environment_ref
    )
    assert volume_entry.model_dump(mode="json") == {
        "path": "/data/report.txt",
        "type": "file",
        "mtime": "2026-04-15T10:06:00",
        "size_bytes": 512,
    }
    assert sandbox.model_dump(mode="json")["tags"] == ["ci", "nightly"]
    assert page.model_dump(mode="json") == {
        "items": [
            {
                "workspace_ref": "mref1.workspace.sig",
                "name": "Acme",
                "source": "authenticated_token",
                "current": True,
            }
        ],
        "next_cursor": "mc1.cursor.sig",
        "truncated": False,
    }
    assert logs_page.model_dump(mode="json") == {
        "entries": [
            {
                "ts": "2026-04-15T10:08:00",
                "source": "app",
                "message": "started",
                "app_ref": "mref1.app.sig",
                "container_ref": "mref1.container.sig",
                "function_id": "fn_456",
                "function_call_id": None,
                "sandbox_ref": "mref1.sandbox.sig",
                "dedup_key": "dedup-1",
            }
        ],
        "summary": {
            "error_signatures": ["timeout"],
            "top_sources": ["app"],
            "total_entries": 1,
            "truncated": False,
            "deduped_count": 0,
        },
        "next_cursor": None,
        "stream_reset": False,
    }


@pytest.mark.parametrize(
    ("factory", "field", "value"),
    [
        (Workspace, "source", "native_id"),
        (Deployment, "status", "deployed"),
        (VolumeEntry, "type", "blob"),
    ],
)
def test_literal_fields_reject_invalid_values(
    factory: type[Workspace] | type[Deployment] | type[VolumeEntry],
    field: str,
    value: str,
) -> None:
    """Literal-constrained public fields reject unsupported values."""

    base_payload: dict[str, object]
    if factory is Workspace:
        base_payload = {
            "workspace_ref": "mref1.workspace.sig",
            "name": "Acme",
            "source": "authenticated_token",
            "current": True,
        }
    elif factory is Deployment:
        base_payload = {
            "version": 1,
            "status": "active",
            "deployed_at": datetime(2026, 4, 15, 10, 0, 0),
            "client_version": "1.4.1",
            "deployed_by": "ronny",
            "rollback_allowed": True,
        }
    else:
        base_payload = {
            "path": "/tmp/file",
            "type": "file",
            "mtime": datetime(2026, 4, 15, 10, 0, 0),
            "size_bytes": 1,
        }

    base_payload[field] = value

    with pytest.raises(ValidationError):
        factory.model_validate(base_payload)
