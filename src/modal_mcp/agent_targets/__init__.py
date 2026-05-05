"""Agent install target contracts for Modal MCP.

Re-exports :class:`AgentTargetContract` as the shared contract dataclass used
by all agent target modules.
"""

from __future__ import annotations

from modal_mcp.agent_targets.contract import AgentTargetContract

__all__ = ["AgentTargetContract"]
