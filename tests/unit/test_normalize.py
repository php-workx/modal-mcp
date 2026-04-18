"""Unit tests for Modal object normalization."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

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
from modal_mcp.domain.refs import RefPayload, decode_ref

SIGNING_KEYS = (("kid1", bytes.fromhex("000102030405060708090a0b0c0d0e0f")),)
NOW = datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC)
TTL = 60


def _decode_ref(token: str) -> RefPayload:
    return decode_ref(token, signing_keys=SIGNING_KEYS, now=int(NOW.timestamp()))


def test_normalize_workspace_environment_and_app_sign_refs() -> None:
    """Core entity normalizers sign refs and preserve the public fields."""

    workspace = normalize_workspace(
        {
            "workspace_id": "ws-123",
            "name": "Acme",
            "workspace_name": "acme-workspace",
            "environment_name": "prod",
            "profile_name": "default",
            "current": True,
        },
        signing_keys=SIGNING_KEYS,
        now=NOW,
        ttl=TTL,
    )
    environment = normalize_environment(
        SimpleNamespace(
            environment_id="env-456",
            name="prod",
            environment_name="prod",
            workspace_name="acme-workspace",
            created_at="2026-04-15T10:01:00Z",
            web_suffix="acme.modal.run",
            is_default=True,
        ),
        signing_keys=SIGNING_KEYS,
        now=NOW,
        ttl=TTL,
    )
    app = normalize_app(
        SimpleNamespace(
            app_id="app-789",
            name="api",
            description="Primary API",
            state="running",
            created_at="2026-04-15T10:02:00Z",
            stopped_at=None,
            n_running_tasks=3,
            environment_name="prod",
            workspace_name="acme-workspace",
        ),
        signing_keys=SIGNING_KEYS,
        now=NOW,
        ttl=TTL,
    )

    assert workspace.model_dump(mode="json") == {
        "workspace_ref": workspace.workspace_ref,
        "name": "Acme",
        "source": "local_profile",
        "current": True,
    }
    workspace_payload = _decode_ref(workspace.workspace_ref)
    assert workspace_payload.id == "ws-123"
    assert workspace_payload.env == "prod"
    assert workspace_payload.ws == "acme-workspace"
    assert workspace_payload.exp == int(NOW.timestamp()) + TTL

    assert environment.model_dump(mode="json") == {
        "environment_ref": environment.environment_ref,
        "name": "prod",
        "is_default": True,
        "created_at": "2026-04-15T10:01:00Z",
        "web_suffix": "acme.modal.run",
    }
    environment_payload = _decode_ref(environment.environment_ref)
    assert environment_payload.id == "env-456"
    assert environment_payload.env == "prod"
    assert environment_payload.ws == "acme-workspace"

    assert app.model_dump(mode="json") == {
        "app_ref": app.app_ref,
        "name": "api",
        "description": "Primary API",
        "state": "running",
        "created_at": "2026-04-15T10:02:00Z",
        "stopped_at": None,
        "n_running_tasks": 3,
        "environment_ref": app.environment_ref,
    }
    app_payload = _decode_ref(app.app_ref)
    assert app_payload.id == "app-789"
    assert app_payload.env == "prod"
    assert app_payload.ws == "acme-workspace"


@pytest.mark.parametrize(
    (
        "payload",
        "expected_status",
        "expected_version",
    ),
    [
        (
            {
                "version": 7,
                "state": "deployed",
                "deployed_at": "2026-04-15T10:03:00Z",
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
                    "commit_timestamp": "2026-04-15T10:00:00Z",
                    "dirty": False,
                    "author_name": "Ada",
                    "author_email": "ada@example.com",
                    "repo_url": "https://example.com/repo",
                },
                "image_digest": "sha256:deadbeef",
            },
            "active",
            7,
        ),
        (
            {
                "version": 8,
                "rolled_back": True,
                "deployed_at": "2026-04-15T10:04:00Z",
                "client_version": "1.4.1",
                "deployed_by": "ronny",
                "rollback_allowed": False,
            },
            "rolled_back",
            8,
        ),
    ],
)
def test_normalize_deployment_preserves_metadata_and_derives_status(
    payload: dict[str, object],
    expected_status: str,
    expected_version: int,
) -> None:
    """Deployment normalization keeps history fields and status derivation stable."""

    deployment = normalize_deployment(payload)

    assert deployment.version == expected_version
    assert deployment.status == expected_status
    assert deployment.deployed_at.isoformat().startswith("2026-04-15T10:0")
    assert deployment.client_version == "1.4.1"
    assert deployment.deployed_by == "ronny"

    if expected_status == "active":
        assert deployment.deployed_by_avatar_url is None
        assert deployment.tag == "release-7"
        assert deployment.rollback_version == 6
        assert deployment.rollback_allowed is True
        assert deployment.image_digest == "sha256:deadbeef"
        assert deployment.commit is not None
        assert deployment.commit.model_dump(mode="json") == {
            "vcs": "git",
            "branch": "main",
            "commit_hash": "abc123",
            "commit_timestamp": "2026-04-15T10:00:00Z",
            "dirty": False,
            "author_name": "Ada",
            "author_email": "ada@example.com",
            "repo_url": "https://example.com/repo",
        }


def test_normalize_container_volume_and_sandbox_use_public_shapes() -> None:
    """Other entity normalizers keep the explicit public identifiers."""

    container = normalize_container(
        SimpleNamespace(
            container_id="container-123",
            task_id="task-123",
            app_id="app-123",
            state="running",
            started_at="2026-04-15T10:05:00Z",
            finished_at=None,
            region="us-east-1",
            workspace_name="acme-workspace",
            environment_name="prod",
        ),
        signing_keys=SIGNING_KEYS,
        now=NOW,
        ttl=TTL,
    )
    volume = normalize_volume(
        {
            "volume_id": "volume-123",
            "name": "uploads",
            "created_at": "2026-04-15T10:06:00Z",
            "created_by": "ronny",
            "environment_name": "prod",
            "workspace_name": "acme-workspace",
        },
        signing_keys=SIGNING_KEYS,
        now=NOW,
        ttl=TTL,
    )
    sandbox = normalize_sandbox(
        {
            "sandbox_id": "sandbox-123",
            "name": "job-runner",
            "app_id": "app-123",
            "created_at": "2026-04-15T10:07:00Z",
            "status": "finished",
            "returncode": 0,
            "tags": ["ci", "nightly"],
            "environment_name": "prod",
            "workspace_name": "acme-workspace",
        },
        signing_keys=SIGNING_KEYS,
        now=NOW,
        ttl=TTL,
    )

    assert container.task_id == "task-123"
    assert _decode_ref(container.container_ref).id == "container-123"
    assert _decode_ref(container.app_ref or "").id == "app-123"
    assert container.state == "running"
    assert container.started_at is not None
    assert container.region == "us-east-1"

    assert volume.name == "uploads"
    assert _decode_ref(volume.volume_ref).id == "volume-123"
    assert _decode_ref(volume.environment_ref).id == "prod"
    assert volume.created_by == "ronny"

    assert sandbox.sandbox_id == "sandbox-123"
    assert _decode_ref(sandbox.sandbox_ref).id == "sandbox-123"
    assert _decode_ref(sandbox.app_ref or "").id == "app-123"
    assert sandbox.tags == ["ci", "nightly"]


def test_normalize_log_batch_redacts_supplied_secrets() -> None:
    """Log batches redact supplied secret strings before they leave the normalizer."""

    secret = "super-secret-token"
    page = normalize_log_batch(
        {
            "entries": [
                {
                    "ts": "2026-04-15T10:08:00Z",
                    "source": "app",
                    "message": f"started with {secret}",
                    "app_id": "app-123",
                    "container_id": "container-456",
                    "function_id": "fn-456",
                    "function_call_id": "call-789",
                    "sandbox_id": "sandbox-123",
                    "dedup_key": secret,
                }
            ],
            "summary": {
                "error_signatures": [f"timeout:{secret}"],
                "top_sources": ["app"],
                "total_entries": 1,
                "truncated": False,
                "deduped_count": 0,
            },
            "next_cursor": "cursor-1",
            "stream_reset": True,
        },
        signing_keys=SIGNING_KEYS,
        now=NOW,
        ttl=TTL,
        secret_strings=[secret],
    )

    entry = page.entries[0]
    assert entry.message == "started with [REDACTED]"
    assert entry.dedup_key == "[REDACTED]"
    assert _decode_ref(entry.app_ref or "").id == "app-123"
    assert _decode_ref(entry.container_ref or "").id == "container-456"
    assert _decode_ref(entry.sandbox_ref or "").id == "sandbox-123"
    assert page.summary.error_signatures == ["timeout:[REDACTED]"]
    assert page.summary.top_sources == ["app"]
    assert page.summary.total_entries == 1
    assert page.summary.truncated is False
    assert page.summary.deduped_count == 0
    assert page.next_cursor == "cursor-1"
    assert page.stream_reset is True
    assert secret not in page.model_dump_json()
