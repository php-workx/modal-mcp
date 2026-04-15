"""Policy decision primitives for Modal MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

READ_ONLY_TOOLSETS = frozenset(
    {"discovery", "apps", "containers", "logs", "volumes", "sandboxes"}
)
CHANGE_TOOLSETS = frozenset({"change", "expert"})
KNOWN_TOOLSETS = READ_ONLY_TOOLSETS | CHANGE_TOOLSETS


class PolicyCode(StrEnum):
    """Stable policy decision codes."""

    ALLOWED = "ALLOWED"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    TOOLSET_DISABLED = "TOOLSET_DISABLED"
    READ_ONLY_BLOCKED = "READ_ONLY_BLOCKED"
    POLICY_BLOCKED = "POLICY_BLOCKED"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Decision returned by policy evaluation."""

    allowed: bool
    code: PolicyCode
    reason: str
    tool_name: str
    toolset: str
    metadata: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )


def _decision(
    *,
    allowed: bool,
    code: PolicyCode,
    reason: str,
    tool_name: str,
    toolset: str,
    metadata: dict[str, Any] | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        allowed=allowed,
        code=code,
        reason=reason,
        tool_name=tool_name,
        toolset=toolset,
        metadata=MappingProxyType(dict(metadata or {})),
    )


def evaluate(
    *,
    tool_name: str,
    toolset: str,
    read_only: bool = True,
    enabled_toolsets: set[str] | frozenset[str] | tuple[str, ...] = READ_ONLY_TOOLSETS,
    metadata: dict[str, Any] | None = None,
) -> PolicyDecision:
    """Evaluate whether a tool is allowed under server policy."""

    normalized_tool = tool_name.strip()
    normalized_toolset = toolset.strip().lower()
    enabled = frozenset(enabled_toolsets)

    if not normalized_tool or normalized_toolset not in KNOWN_TOOLSETS:
        return _decision(
            allowed=False,
            code=PolicyCode.UNKNOWN_TOOL,
            reason="unknown tool or toolset",
            tool_name=normalized_tool,
            toolset=normalized_toolset,
            metadata=metadata,
        )

    if normalized_toolset not in enabled:
        return _decision(
            allowed=False,
            code=PolicyCode.TOOLSET_DISABLED,
            reason="toolset is disabled",
            tool_name=normalized_tool,
            toolset=normalized_toolset,
            metadata=metadata,
        )

    if read_only and normalized_toolset in CHANGE_TOOLSETS:
        return _decision(
            allowed=False,
            code=PolicyCode.READ_ONLY_BLOCKED,
            reason="read-only mode blocks mutating toolsets",
            tool_name=normalized_tool,
            toolset=normalized_toolset,
            metadata=metadata,
        )

    return _decision(
        allowed=True,
        code=PolicyCode.ALLOWED,
        reason="allowed",
        tool_name=normalized_tool,
        toolset=normalized_toolset,
        metadata=metadata,
    )


__all__ = [
    "CHANGE_TOOLSETS",
    "KNOWN_TOOLSETS",
    "READ_ONLY_TOOLSETS",
    "PolicyCode",
    "PolicyDecision",
    "evaluate",
]
