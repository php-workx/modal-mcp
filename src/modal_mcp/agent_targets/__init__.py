"""Agent install target contracts and a name-based registry.

Each entry in :data:`TARGETS` is a tuple of ``(name, module)`` pairs.  Lookup
via :func:`get_target` returns the module object; callers then call
``module.install(...)`` or ``module.render(...)`` directly.  This avoids
hard-coding ``if name == 'codex'`` branches inside the CLI layer.
"""

from __future__ import annotations

from types import ModuleType
from typing import Final

from modal_mcp.agent_targets import claude, codex
from modal_mcp.agent_targets.contract import AgentTargetContract

#: Name → module map.  Each module MUST expose ``install`` and ``render``
#: functions matching the agent-target protocol.
_TARGETS: Final[dict[str, ModuleType]] = {
    "codex": codex,
    "claude": claude,
    "claude_desktop": claude,  # alias
}

# Register optional targets only when present.
try:
    from modal_mcp.agent_targets import cursor as _cursor

    _TARGETS["cursor"] = _cursor
except ImportError:
    pass

TARGETS: Final[tuple[tuple[str, ModuleType], ...]] = tuple(_TARGETS.items())


def get_target(name: str) -> ModuleType:
    """Return the agent-target module for *name* (case-insensitive).

    Raises :class:`ValueError` when *name* is not a known target.
    """
    key = name.lower()
    if key not in _TARGETS:
        supported = ", ".join(sorted(_TARGETS))
        msg = f"Unknown agent target: {name!r}. Supported: {supported}."
        raise ValueError(msg)
    return _TARGETS[key]


__all__ = [
    "AgentTargetContract",
    "TARGETS",
    "get_target",
]
