"""Process-wide Modal adapter registry."""

from __future__ import annotations

from modal_mcp.adapters.base import ModalAdapter

_BOUND_ADAPTER: ModalAdapter | None = None


def bind_modal_adapter(adapter: ModalAdapter | None) -> None:
    """Bind or clear the process-wide Modal adapter."""

    global _BOUND_ADAPTER
    _BOUND_ADAPTER = adapter


def get_modal_adapter() -> ModalAdapter:
    """Return the bound Modal adapter.

    Raises:
        LookupError: if no adapter has been bound for this process.
    """

    if _BOUND_ADAPTER is None:
        msg = "no Modal adapter has been bound for this process"
        raise LookupError(msg)
    return _BOUND_ADAPTER


__all__ = ["bind_modal_adapter", "get_modal_adapter"]
