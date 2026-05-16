# tests/unit/test_register_read_toolset.py
"""Unit tests for register_read_toolset factory."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP
from pydantic import BaseModel

from modal_mcp.toolsets._common import register_read_toolset


class Widget(BaseModel):
    id: str
    name: str


def _list_widgets(
    environment_name: str | None = None,
) -> tuple[list[Widget], list[str]]:
    return [Widget(id="w1", name="foo")], []


def _get_widget(widget_ref: str) -> Widget | None:
    return Widget(id="w1", name="foo") if widget_ref == "w1" else None


@pytest.mark.asyncio
async def test_register_read_toolset_creates_two_tools() -> None:
    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_widgets,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "modal_list_widgets" in names
    assert "modal_get_widget" in names


@pytest.mark.asyncio
async def test_list_tool_has_read_only_annotations() -> None:
    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_widgets,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    tools = await mcp.list_tools()
    tool_map = {t.name: t for t in tools}
    assert tool_map["modal_list_widgets"].annotations.readOnlyHint is True
    assert tool_map["modal_list_widgets"].annotations.idempotentHint is True
    assert tool_map["modal_get_widget"].annotations.readOnlyHint is True
    assert tool_map["modal_get_widget"].annotations.idempotentHint is True


@pytest.mark.asyncio
async def test_list_tool_has_correct_tags() -> None:
    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_widgets,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    tools = await mcp.list_tools()
    tool_map = {t.name: t for t in tools}
    assert tool_map["modal_list_widgets"].tags == {"widgets"}
    assert tool_map["modal_get_widget"].tags == {"widgets"}


@pytest.mark.asyncio
async def test_list_tool_returns_page_envelope_partial() -> None:
    """list_fn result is wrapped via page_envelope_partial (warnings preserved)."""
    from fastmcp.tools.base import ToolResult

    warned_items: list[Widget] = [Widget(id="w2", name="bar")]
    warned: list[str] = ["normalization warning"]

    def _list_with_warnings(
        environment_name: str | None = None,
    ) -> tuple[list[Widget], list[str]]:
        return warned_items, warned

    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_with_warnings,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    result: ToolResult = await mcp.call_tool("modal_list_widgets", {})
    # structured content contains items and warnings
    content = result.structured_content
    assert content is not None
    assert content["data"]["items"][0]["id"] == "w2"
    assert "normalization warning" in content["warnings"]


@pytest.mark.asyncio
async def test_get_tool_returns_not_found_on_miss() -> None:
    """get_fn returning None is converted to not_found envelope."""
    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_widgets,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    result = await mcp.call_tool("modal_get_widget", {"widget_ref": "missing"})
    content = result.structured_content
    assert content is not None
    assert content["error"]["code"] == "NOT_FOUND"
    assert "widget not found: missing" in content["error"]["message"]


@pytest.mark.asyncio
async def test_list_tool_passes_environment_name() -> None:
    """environment_name kwarg is forwarded to list_fn."""
    received_env: list[str | None] = []

    def _list_capture(
        environment_name: str | None = None,
    ) -> tuple[list[Widget], list[str]]:
        received_env.append(environment_name)
        return [], []

    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_capture,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
    )
    await mcp.call_tool("modal_list_widgets", {"environment_name": "staging"})
    assert received_env == ["staging"]


@pytest.mark.asyncio
async def test_list_tool_extra_params_forwarded() -> None:
    """Extra params declared via extra_list_params are forwarded to list_fn."""
    received_app_ref: list[str | None] = []

    def _list_with_app_ref(
        environment_name: str | None = None,
        app_ref: str | None = None,
    ) -> tuple[list[Widget], list[str]]:
        received_app_ref.append(app_ref)
        return [], []

    mcp: FastMCP = FastMCP("test")
    register_read_toolset(
        mcp=mcp,
        entity_name="widget",
        list_fn=_list_with_app_ref,
        get_fn=_get_widget,
        get_param_name="widget_ref",
        not_found_message_template="widget not found: {ref}",
        tags={"widgets"},
        extra_list_params=["app_ref"],
    )
    await mcp.call_tool("modal_list_widgets", {"app_ref": "mref1.app"})
    assert received_app_ref == ["mref1.app"]
