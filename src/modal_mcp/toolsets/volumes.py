"""Volume read-only tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from modal_mcp.adapters.registry import get_modal_adapter
from modal_mcp.domain.envelope import ToolEnvelope
from modal_mcp.domain.models import Page, VolumeEntry, VolumeSummary
from modal_mcp.toolsets._common import (
    READ_ONLY_ANNOTATIONS,
    envelope,
    not_found,
    page_envelope,
)


class VolumeText(BaseModel):
    """Text file payload returned from a volume."""

    content: str
    truncated: bool


def register_volume_tools(mcp: FastMCP[Any]) -> None:
    """Register volume tools with read-only annotations."""

    @mcp.tool(
        name="modal_list_volumes",
        tags={"volumes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_list_volumes(
        environment_name: str | None = None,
    ) -> ToolEnvelope[Page[VolumeSummary]]:
        return page_envelope(get_modal_adapter().list_volumes(environment_name))

    @mcp.tool(
        name="modal_ls_volume",
        tags={"volumes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_ls_volume(
        volume_ref: str,
        path: str = "/",
        recursive: bool = False,
        max_entries: int | None = None,
    ) -> ToolEnvelope[Page[VolumeEntry]]:
        return page_envelope(
            get_modal_adapter().ls_volume(
                volume_ref,
                path,
                recursive=recursive,
                max_entries=max_entries,
            )
        )

    @mcp.tool(
        name="modal_read_volume_text",
        tags={"volumes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_read_volume_text(
        volume_ref: str,
        path: str,
        max_bytes: Annotated[int, Field(ge=1, le=1_048_576)] = 262_144,
    ) -> ToolEnvelope[VolumeText]:
        raw_content = get_modal_adapter().read_volume_text(
            volume_ref,
            path,
            max_bytes=max_bytes,
        )
        encoded = raw_content.encode("utf-8")
        truncated = len(encoded) > max_bytes
        content = encoded[:max_bytes].decode("utf-8", errors="replace")
        return envelope(VolumeText(content=content, truncated=truncated))

    @mcp.tool(
        name="modal_stat_volume_path",
        tags={"volumes"},
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def modal_stat_volume_path(
        volume_ref: str,
        path: str,
    ) -> ToolEnvelope[VolumeEntry]:
        entry = get_modal_adapter().stat_volume_path(volume_ref, path)
        if entry is None:
            return not_found(f"volume path not found: {path}")
        return envelope(entry)


__all__ = ["VolumeText", "register_volume_tools"]
