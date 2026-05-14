"""Normalize Modal SDK/proto-like objects into public domain models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from modal_mcp.domain.models import (
    App,
    CommitInfo,
    Container,
    Deployment,
    Environment,
    LogEntry,
    LogsPage,
    LogSummary,
    SandboxSummary,
    VolumeSummary,
    Workspace,
)
from modal_mcp.domain.refs import RefPayload, encode_ref

REF_PREFIX = "mref1"
REDACTED_TEXT = "[REDACTED]"


def _lookup(raw: Any, name: str, default: Any = None) -> Any:
    if raw is None:
        return default
    if isinstance(raw, Mapping):
        return raw.get(name, default)
    return getattr(raw, name, default)


def _first(raw: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = _lookup(raw, name, default=None)
        if value is not None:
            return value
    return default


def _normalize_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if hasattr(value, "value") and not isinstance(value, (bytes, bytearray)):
        enum_value = value.value
        if isinstance(enum_value, str):
            return enum_value
        if enum_value is not None:
            return str(enum_value)
    return str(value)


def _normalize_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _normalize_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    return int(value)


def _normalize_datetime(value: Any, default: datetime | None = None) -> datetime | None:
    if value is None:
        return default
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    text = _normalize_str(value)
    if not text:
        return default
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return datetime.fromisoformat(text)


def _normalize_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [_normalize_str(value)]
    if isinstance(value, Mapping):
        return [_normalize_str(f"{key}={item}") for key, item in value.items()]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_normalize_str(item) for item in value]
    return [_normalize_str(value)]


def _normalize_text(value: Any, secrets: Sequence[str] | None = None) -> str:
    text = _normalize_str(value)
    if not text or not secrets:
        return text
    redacted = text
    for secret in sorted(
        {secret for secret in secrets if secret}, key=len, reverse=True
    ):
        redacted = redacted.replace(secret, REDACTED_TEXT)
    return redacted


def _normalize_context(raw: Any, *, entity_name: str | None = None) -> tuple[str, str]:
    environment_name = _normalize_str(
        _first(raw, "environment_name", "env_name", "environment", "env"),
        default="",
    )
    workspace_name = _normalize_str(
        _first(raw, "workspace_name", "ws_name", "workspace", "ws"),
        default="",
    )
    if not workspace_name and entity_name is not None:
        workspace_name = entity_name
    return environment_name, workspace_name


def _maybe_existing_ref(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(f"{REF_PREFIX}."):
        return value
    return None


def _normalize_ref(
    raw: Any,
    *,
    kind: str,
    id_names: Sequence[str],
    entity_name: str | None = None,
    required: bool = True,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    now: datetime | int | None = None,
    ttl: int = 3600,
) -> str:
    existing = _maybe_existing_ref(
        _first(raw, f"{kind}_ref", f"{kind}Ref", default=None)
    )
    if existing is not None:
        return existing

    raw_id = _normalize_str(_first(raw, *id_names, default=""))
    if not raw_id and entity_name is not None:
        raw_id = entity_name
    if not raw_id:
        if not required:
            raw_id = f"unknown-{kind}"
        else:
            msg = f"{kind} id is required"
            raise ValueError(msg)
    environment_name, workspace_name = _normalize_context(raw, entity_name=entity_name)
    payload = RefPayload(
        id=raw_id,
        env=environment_name,
        ws=workspace_name,
        exp=_now_epoch(now) + ttl,
    )
    return encode_ref(payload, signing_keys=signing_keys)


def _now_epoch(now: datetime | int | None) -> int:
    if now is None:
        return int(datetime.now(UTC).timestamp())
    if isinstance(now, int):
        return now
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return int(now.timestamp())


def _normalize_commit_info(raw: Any) -> CommitInfo:
    commit_timestamp = _normalize_datetime(
        _first(raw, "commit_timestamp", "timestamp", "committed_at", "created_at"),
        default=None,
    )
    if commit_timestamp is None:
        msg = "commit timestamp is required"
        raise ValueError(msg)

    vcs = _normalize_str(_first(raw, "vcs", "version_control_system"), default="")
    branch = _normalize_str(_first(raw, "branch", "ref_name"), default="")
    commit_hash = _normalize_str(
        _first(raw, "commit_hash", "hash", "sha", "revision"), default=""
    )
    if not vcs or not branch or not commit_hash:
        msg = "commit vcs, branch, and commit_hash are required"
        raise ValueError(msg)

    return CommitInfo(
        vcs=vcs,
        branch=branch,
        commit_hash=commit_hash,
        commit_timestamp=commit_timestamp,
        dirty=_normalize_bool(_first(raw, "dirty", "is_dirty"), default=False),
        author_name=_normalize_str(_first(raw, "author_name", "author"), default="")
        or None,
        author_email=_normalize_str(_first(raw, "author_email", "email"), default="")
        or None,
        repo_url=_normalize_str(_first(raw, "repo_url", "repository_url"), default="")
        or None,
    )


def _normalize_source(raw: Any) -> Literal["local_profile", "authenticated_token"]:
    source = _normalize_str(_first(raw, "source", "workspace_source", "profile_source"))
    if source:
        source_normalized = source.replace("-", "_").lower()
        if source_normalized in {"local_profile", "local", "profile"}:
            return "local_profile"
        if source_normalized in {"authenticated_token", "token", "authenticated"}:
            return "authenticated_token"
    if (
        _first(raw, "profile_name", "profile", "local_profile", "config_profile")
        is not None
    ):
        return "local_profile"
    return "authenticated_token"


def _normalize_status(
    raw: Any,
) -> Literal["active", "superseded", "rolled_back", "unknown"]:
    explicit_status = _normalize_str(
        _first(raw, "status", "state", "deployment_status")
    )
    status_map: dict[str, Literal["active", "superseded", "rolled_back", "unknown"]] = {
        "active": "active",
        "current": "active",
        "latest": "active",
        "deployed": "active",
        "running": "active",
        "live": "active",
        "primary": "active",
        "superseded": "superseded",
        "inactive": "superseded",
        "previous": "superseded",
        "older": "superseded",
        "replaced": "superseded",
        "rolled_back": "rolled_back",
        "rollback": "rolled_back",
        "rolledback": "rolled_back",
        "reverted": "rolled_back",
        "unknown": "unknown",
    }
    normalized_status = explicit_status.replace("-", "_").replace(" ", "_").lower()
    if normalized_status in status_map:
        return status_map[normalized_status]

    rollback_markers = (
        "rolled_back",
        "is_rolled_back",
        "rolled_back_at",
        "rollbacked",
        "was_rolled_back",
        "rollback_applied",
    )
    if any(
        _normalize_bool(_first(raw, marker), default=False)
        for marker in rollback_markers
    ):
        return "rolled_back"

    active_markers = (
        "active",
        "is_active",
        "current",
        "is_current",
        "latest",
        "is_latest",
        "current_version",
        "is_current_version",
    )
    if any(
        _normalize_bool(_first(raw, marker), default=False) for marker in active_markers
    ):
        return "active"

    superseded_markers = (
        "superseded",
        "is_superseded",
        "previous",
        "is_previous",
        "inactive",
        "is_inactive",
    )
    if any(
        _normalize_bool(_first(raw, marker), default=False)
        for marker in superseded_markers
    ):
        return "superseded"

    current_version = _first(raw, "current_version", "active_version")
    version = _first(raw, "version", "deployment_version")
    if (
        current_version is not None
        and version is not None
        and str(current_version) == str(version)
    ):
        return "active"

    return "unknown"


def normalize_workspace(
    raw: Any,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    now: datetime | int | None = None,
    ttl: int = 3600,
) -> Workspace:
    """Normalize a workspace-like object into a public workspace summary."""

    if isinstance(raw, Workspace):
        return raw

    entity_name = _normalize_str(_first(raw, "name", "workspace_name"), default="")
    return Workspace(
        workspace_ref=_normalize_ref(
            raw,
            kind="workspace",
            id_names=("workspace_id", "id", "workspace_ref"),
            entity_name=entity_name or None,
            required=False,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        ),
        name=entity_name,
        source=_normalize_source(raw),
        current=_normalize_bool(
            _first(raw, "current", "is_current", "active"), default=False
        ),
    )


class WorkspaceNormalizer:
    def __init__(self, signing_keys=None, ttl: int = 3600) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[Workspace | None, list[str]]:
        try:
            entity = normalize_workspace(
                raw, signing_keys=self._signing_keys, ttl=self._ttl
            )
            return entity, []
        except Exception as exc:
            return None, [str(exc)]


def normalize_environment(
    raw: Any,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    now: datetime | int | None = None,
    ttl: int = 3600,
) -> Environment:
    """Normalize an environment-like object into a public environment summary."""

    if isinstance(raw, Environment):
        return raw

    entity_name = _normalize_str(_first(raw, "name", "environment_name"), default="")
    return Environment(
        environment_ref=_normalize_ref(
            raw,
            kind="environment",
            id_names=("environment_id", "id", "environment_ref"),
            entity_name=entity_name or None,
            required=False,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        ),
        name=entity_name,
        is_default=_normalize_bool(
            _first(raw, "is_default", "default", "current", "active"), default=False
        ),
        created_at=_normalize_datetime(
            _first(raw, "created_at", "created", "created_timestamp")
        ),
        web_suffix=_normalize_str(_first(raw, "web_suffix", "web_domain"), default="")
        or None,
    )


class EnvironmentNormalizer:
    def __init__(self, signing_keys=None, ttl: int = 3600) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[Environment | None, list[str]]:
        try:
            entity = normalize_environment(
                raw, signing_keys=self._signing_keys, ttl=self._ttl
            )
            return entity, []
        except Exception as exc:
            return None, [str(exc)]


def normalize_app(
    raw: Any,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    now: datetime | int | None = None,
    ttl: int = 3600,
) -> App:
    """Normalize an app-like object into a public app summary."""

    if isinstance(raw, App):
        return raw

    name = _normalize_str(_first(raw, "name", "app_name"), default="")
    created_at = _normalize_datetime(
        _first(raw, "created_at", "created", "created_timestamp")
    )
    return App(
        app_ref=_normalize_ref(
            raw,
            kind="app",
            id_names=("app_id", "id", "app_ref"),
            entity_name=name or None,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        ),
        name=name,
        description=_normalize_str(
            _first(raw, "description", "summary", "notes"), default=""
        ),
        state=_normalize_str(
            _first(raw, "state", "status", "app_state"), default="unknown"
        ),
        created_at=created_at,
        stopped_at=_normalize_datetime(
            _first(raw, "stopped_at", "stopped", "terminated_at")
        ),
        n_running_tasks=_normalize_int(
            _first(
                raw,
                "n_running_tasks",
                "running_tasks",
                "num_running_tasks",
                "task_count",
            ),
            default=0,
        ),
        environment_ref=_normalize_ref(
            raw,
            kind="environment",
            id_names=("environment_id", "environment_ref", "env_id", "env_ref"),
            entity_name=_normalize_str(
                _first(raw, "environment_name", "env_name"), default=""
            )
            or None,
            required=False,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        ),
    )


class AppNormalizer:
    def __init__(self, signing_keys=None, ttl: int = 3600) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[App | None, list[str]]:
        try:
            entity = normalize_app(
                raw, signing_keys=self._signing_keys, ttl=self._ttl
            )
            return entity, []
        except Exception as exc:
            return None, [str(exc)]


def normalize_deployment(
    raw: Any,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    now: datetime | int | None = None,
    ttl: int = 3600,
) -> Deployment:
    """Normalize a deployment history entry into the public deployment model."""

    if isinstance(raw, Deployment):
        return raw

    commit_raw = _first(raw, "commit", "commit_info", "git_commit")
    commit = _normalize_commit_info(commit_raw) if commit_raw is not None else None
    deployed_at = _normalize_datetime(
        _first(raw, "deployed_at", "created_at", "timestamp")
    )
    if deployed_at is None:
        msg = "deployment timestamp is required"
        raise ValueError(msg)
    version = _first(raw, "version", "deployment_version")
    if version is None:
        msg = "deployment version is required"
        raise ValueError(msg)
    client_version = _normalize_str(
        _first(raw, "client_version", "sdk_version", "modal_version"), default=""
    )
    if not client_version:
        msg = "deployment client_version is required"
        raise ValueError(msg)
    deployed_by = _normalize_str(
        _first(raw, "deployed_by", "user", "author"), default=""
    )
    if not deployed_by:
        msg = "deployment deployed_by is required"
        raise ValueError(msg)

    return Deployment(
        version=_normalize_int(version, default=0),
        status=_normalize_status(raw),
        deployed_at=deployed_at,
        client_version=client_version,
        deployed_by=deployed_by,
        deployed_by_avatar_url=_normalize_str(
            _first(raw, "deployed_by_avatar_url", "avatar_url"), default=""
        )
        or None,
        tag=_normalize_str(_first(raw, "tag", "version_tag"), default="") or None,
        rollback_version=_normalize_int(
            _first(raw, "rollback_version", "rollback_to_version"), default=0
        )
        or None,
        rollback_allowed=_normalize_bool(
            _first(raw, "rollback_allowed", "can_rollback"), default=False
        ),
        commit=commit,
        image_digest=_normalize_str(_first(raw, "image_digest", "digest"), default="")
        or None,
    )


class DeploymentNormalizer:
    def __init__(self, signing_keys=None, ttl: int = 3600) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[Deployment | None, list[str]]:
        try:
            entity = normalize_deployment(
                raw, signing_keys=self._signing_keys, ttl=self._ttl
            )
            return entity, []
        except Exception as exc:
            return None, [str(exc)]


def normalize_container(
    raw: Any,
    *,
    hint_task_id: str | None = None,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    now: datetime | int | None = None,
    ttl: int = 3600,
) -> Container:
    """Normalize a container-like object into the public container summary."""

    if isinstance(raw, Container):
        return raw

    task_id = _normalize_str(
        _first(
            raw,
            "task_id",
            "taskId",
            "container_task_id",
            "container_id",
            "tid",
            "id",
        ),
        default="",
    )
    if not task_id:
        task_id = hint_task_id or ""
    if not task_id:
        msg = "container task_id is required"
        raise ValueError(msg)
    return Container(
        container_ref=_normalize_ref(
            raw,
            kind="container",
            id_names=("container_id", "id", "container_ref"),
            entity_name=(
                _normalize_str(_first(raw, "name", "container_name"), default="")
                or task_id
                or None
            ),
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        ),
        task_id=task_id,
        app_ref=_normalize_ref(
            raw,
            kind="app",
            id_names=("app_id", "app_ref", "app_name"),
            entity_name=_normalize_str(
                _first(raw, "app_name", "environment_name"), default=""
            )
            or None,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        )
        if _first(raw, "app_id", "app_ref", "app_name") is not None
        or _first(raw, "app_name") is not None
        else None,
        function_id=_normalize_str(_first(raw, "function_id", "functionId"), default="")
        or None,
        function_call_id=_normalize_str(
            _first(raw, "function_call_id", "functionCallId"), default=""
        )
        or None,
        state=_normalize_str(_first(raw, "state", "status"), default="unknown"),
        started_at=_normalize_datetime(
            _first(raw, "started_at", "started", "created_at")
        ),
        finished_at=_normalize_datetime(
            _first(raw, "finished_at", "finished", "stopped_at")
        ),
        region=_normalize_str(_first(raw, "region", "location"), default="") or None,
    )


class ContainerNormalizer:
    def __init__(self, signing_keys=None, ttl: int = 3600) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(
        self, raw: Any, *, hint_task_id: str | None = None
    ) -> tuple[Container | None, list[str]]:
        try:
            entity = normalize_container(
                raw,
                hint_task_id=hint_task_id,
                signing_keys=self._signing_keys,
                ttl=self._ttl,
            )
            return entity, []
        except Exception as exc:
            return None, [str(exc)]


def normalize_volume(
    raw: Any,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    now: datetime | int | None = None,
    ttl: int = 3600,
) -> VolumeSummary:
    """Normalize a volume-like object into the public volume summary."""

    if isinstance(raw, VolumeSummary):
        return raw

    name = _normalize_str(_first(raw, "name", "volume_name"), default="")
    if not name:
        msg = "volume name is required"
        raise ValueError(msg)
    created_at = _normalize_datetime(_first(raw, "created_at", "created", "timestamp"))
    if created_at is None:
        msg = "volume created_at is required"
        raise ValueError(msg)
    return VolumeSummary(
        volume_ref=_normalize_ref(
            raw,
            kind="volume",
            id_names=("volume_id", "id", "volume_ref"),
            entity_name=_normalize_str(_first(raw, "name", "volume_name"), default="")
            or None,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        ),
        name=name,
        created_at=created_at,
        created_by=_normalize_str(_first(raw, "created_by", "owner"), default="")
        or None,
        environment_ref=_normalize_ref(
            raw,
            kind="environment",
            id_names=("environment_id", "environment_ref", "env_id", "env_ref"),
            entity_name=_normalize_str(
                _first(raw, "environment_name", "env_name"), default=""
            )
            or None,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        ),
    )


class VolumeNormalizer:
    def __init__(self, signing_keys=None, ttl: int = 3600) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[VolumeSummary | None, list[str]]:
        try:
            entity = normalize_volume(
                raw, signing_keys=self._signing_keys, ttl=self._ttl
            )
            return entity, []
        except Exception as exc:
            return None, [str(exc)]


def normalize_sandbox(
    raw: Any,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    now: datetime | int | None = None,
    ttl: int = 3600,
) -> SandboxSummary:
    """Normalize a sandbox-like object into the public sandbox summary."""

    if isinstance(raw, SandboxSummary):
        return raw

    sandbox_id = _normalize_str(
        _first(raw, "sandbox_id", "id", "sandboxId"), default=""
    )
    if not sandbox_id:
        msg = "sandbox sandbox_id is required"
        raise ValueError(msg)
    tags = _normalize_str_list(_first(raw, "tags", "tag_list"))
    created_at = _normalize_datetime(_first(raw, "created_at", "created", "timestamp"))
    if created_at is None:
        msg = "sandbox created_at is required"
        raise ValueError(msg)

    return SandboxSummary(
        sandbox_ref=_normalize_ref(
            raw,
            kind="sandbox",
            id_names=("sandbox_id", "id", "sandbox_ref"),
            entity_name=_normalize_str(_first(raw, "name", "sandbox_name"), default="")
            or None,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        ),
        sandbox_id=sandbox_id,
        app_ref=_normalize_ref(
            raw,
            kind="app",
            id_names=("app_id", "app_ref"),
            entity_name=_normalize_str(
                _first(raw, "app_name", "environment_name"), default=""
            )
            or None,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        )
        if _first(raw, "app_id", "app_ref", "app_name") is not None
        else None,
        name=_normalize_str(_first(raw, "name", "sandbox_name"), default="") or None,
        created_at=created_at,
        status=_normalize_str(_first(raw, "status", "state"), default="unknown"),
        returncode=_normalize_int(_first(raw, "returncode", "exit_code"), default=0)
        if _first(raw, "returncode", "exit_code") is not None
        else None,
        tags=tags,
    )


class SandboxNormalizer:
    def __init__(self, signing_keys=None, ttl: int = 3600) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl

    def normalize(self, raw: Any) -> tuple[SandboxSummary | None, list[str]]:
        try:
            entity = normalize_sandbox(
                raw, signing_keys=self._signing_keys, ttl=self._ttl
            )
            return entity, []
        except Exception as exc:
            return None, [str(exc)]


def _normalize_log_entry(
    raw: Any,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    now: datetime | int | None = None,
    ttl: int = 3600,
    secret_strings: Sequence[str] | None = None,
) -> LogEntry:
    ts = _normalize_datetime(_first(raw, "ts", "timestamp", "time", "created_at"))
    return LogEntry(
        ts=ts,
        source=_normalize_text(
            _first(raw, "source", "stream", "origin"), secret_strings
        ),
        message=_normalize_text(
            _first(raw, "message", "text", "content", "line"),
            secret_strings,
        ),
        app_ref=_normalize_ref(
            raw,
            kind="app",
            id_names=("app_id", "app_ref", "app_name"),
            entity_name=_normalize_str(
                _first(raw, "app_name", "environment_name"), default=""
            )
            or None,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        )
        if _first(raw, "app_id", "app_ref", "app_name") is not None
        or _first(raw, "app_name") is not None
        else None,
        container_ref=_normalize_ref(
            raw,
            kind="container",
            id_names=("container_id", "container_ref"),
            entity_name=_normalize_str(_first(raw, "container_name"), default="")
            or None,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        )
        if _first(raw, "container_id", "container_ref") is not None
        else None,
        function_id=_normalize_text(
            _first(raw, "function_id", "functionId"),
            secret_strings,
        )
        or None,
        function_call_id=_normalize_text(
            _first(raw, "function_call_id", "functionCallId"),
            secret_strings,
        )
        or None,
        sandbox_ref=_normalize_ref(
            raw,
            kind="sandbox",
            id_names=("sandbox_id", "sandbox_ref"),
            entity_name=_normalize_str(_first(raw, "sandbox_name"), default="") or None,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
        )
        if _first(raw, "sandbox_id", "sandbox_ref", "sandbox_name") is not None
        else None,
        dedup_key=_normalize_text(
            _first(raw, "dedup_key", "dedupKey", "key"), secret_strings
        )
        or None,
    )


def _normalize_log_summary(
    raw: Any, *, secret_strings: Sequence[str] | None = None
) -> LogSummary:
    return LogSummary(
        error_signatures=[
            _normalize_text(item, secret_strings)
            for item in _normalize_str_list(
                _first(raw, "error_signatures", "signatures", "errors")
            )
        ],
        top_sources=[
            _normalize_text(item, secret_strings)
            for item in _normalize_str_list(
                _first(raw, "top_sources", "sources", "top")
            )
        ],
        total_entries=_normalize_int(
            _first(raw, "total_entries", "count", "entry_count", "total"), default=0
        ),
        truncated=_normalize_bool(
            _first(raw, "truncated", "is_truncated", "partial"), default=False
        ),
        deduped_count=_normalize_int(
            _first(raw, "deduped_count", "deduped", "dedup_count"), default=0
        ),
    )


def normalize_log_batch(
    raw: Any,
    *,
    signing_keys: Sequence[tuple[str, bytes]] | Sequence[Any] | None = None,
    now: datetime | int | None = None,
    ttl: int = 3600,
    secret_strings: Sequence[str] | None = None,
) -> LogsPage:
    """Normalize a batch of logs and redact caller-supplied secret strings."""

    if isinstance(raw, LogsPage):
        return raw

    entries_raw = _first(raw, "entries", "matches", "items", "logs", default=[])
    entries = [
        _normalize_log_entry(
            entry,
            signing_keys=signing_keys,
            now=now,
            ttl=ttl,
            secret_strings=secret_strings,
        )
        for entry in _normalize_str_list(entries_raw)
    ]
    if entries_raw and not isinstance(entries_raw, (str, bytes, bytearray)):
        entries = [
            _normalize_log_entry(
                entry,
                signing_keys=signing_keys,
                now=now,
                ttl=ttl,
                secret_strings=secret_strings,
            )
            for entry in entries_raw
        ]

    summary_raw = _first(raw, "summary", "log_summary", "stats", default=None)
    summary = _normalize_log_summary(summary_raw or raw, secret_strings=secret_strings)
    if not summary.total_entries:
        summary = summary.model_copy(
            update={
                "total_entries": len(entries),
                "truncated": _normalize_bool(
                    _first(raw, "truncated", "is_truncated", "partial", default=False),
                    default=False,
                ),
            }
        )

    return LogsPage(
        entries=entries,
        summary=summary,
        next_cursor=_normalize_text(_first(raw, "next_cursor", "cursor", "nextToken"))
        or None,
        stream_reset=_normalize_bool(
            _first(raw, "stream_reset", "reset", "streamReset"), default=False
        ),
    )


class LogBatchNormalizer:
    def __init__(self, signing_keys=None, ttl: int = 3600, secret_strings=None) -> None:
        self._signing_keys = signing_keys
        self._ttl = ttl
        self._secret_strings = secret_strings

    def normalize(self, raw: Any) -> tuple[LogsPage | None, list[str]]:
        try:
            return normalize_log_batch(
                raw,
                signing_keys=self._signing_keys,
                ttl=self._ttl,
                secret_strings=self._secret_strings,
            ), []
        except Exception as exc:
            return None, [str(exc)]


__all__ = [
    "AppNormalizer",
    "ContainerNormalizer",
    "DeploymentNormalizer",
    "EnvironmentNormalizer",
    "LogBatchNormalizer",
    "SandboxNormalizer",
    "VolumeNormalizer",
    "WorkspaceNormalizer",
    "normalize_app",
    "normalize_container",
    "normalize_deployment",
    "normalize_environment",
    "normalize_log_batch",
    "normalize_sandbox",
    "normalize_volume",
    "normalize_workspace",
]
