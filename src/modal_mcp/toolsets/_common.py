"""Shared helpers for FastMCP toolsets."""

from __future__ import annotations

import keyword
import re
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from modal_mcp.domain.envelope import ToolEnvelope, error_result, ok
from modal_mcp.domain.errors import ErrorCode, ModalAdapterError
from modal_mcp.domain.models import Page

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _assert_valid_param_name(name: str) -> None:
    if not _IDENT_RE.match(name) or keyword.iskeyword(name):
        raise ValueError(f"Invalid Python identifier for exec(): {name!r}")


READ_ONLY_ANNOTATIONS = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
MUTATING_ANNOTATIONS = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
REQUEST_ID = "tool-call"

_T = TypeVar("_T", bound=BaseModel)


def envelope[M: BaseModel](data: M) -> ToolEnvelope[M]:
    """Wrap a concrete model in the standard success envelope."""

    return ok(data, request_id=REQUEST_ID)


def page_envelope[M: BaseModel](items: Sequence[M]) -> ToolEnvelope[Page[M]]:
    """Wrap a sequence in the standard paged success envelope."""

    return ok(
        Page[M](items=list(items), truncated=False),
        request_id=REQUEST_ID,
    )


def page_envelope_partial[M: BaseModel](
    items: Sequence[M],
    warnings: Sequence[str],
) -> ToolEnvelope[Page[M]]:
    """Wrap a partial sequence with normalization warnings in a success envelope."""

    return ok(
        Page[M](items=list(items), truncated=False),
        request_id=REQUEST_ID,
        warnings=list(warnings),
    )


def not_found(message: str) -> ToolEnvelope[Any]:
    """Return a normalized not-found tool error."""

    return error_result(
        ModalAdapterError(ErrorCode.NOT_FOUND, message),
        request_id=REQUEST_ID,
    )


def disabled_error(tool_name: str, details: dict[str, Any]) -> ToolEnvelope[Any]:
    """Return a normalized disabled-capability error."""

    return error_result(
        ModalAdapterError(
            ErrorCode.POLICY_BLOCKED,
            f"{tool_name} is disabled in Modal MCP v1",
            details=details,
        ),
        request_id=REQUEST_ID,
    )


# ---------------------------------------------------------------------------
# register_read_toolset — standard list/get factory
#
# Scope: encodes the list/get pattern only (modal_list_{entity} +
# modal_get_{entity}).  Tools with unique parameter shapes or empty-result
# hints are NOT covered:
#   • modal_get_container_logs  — unique time-range params + empty-log hint
#   • modal_ls_volume / modal_read_volume_text / modal_stat_volume_path
#     — volume-path params not part of the list/get pattern
#   • modal_get_sandbox_stdio — bounded-buffer params
# These keep custom registration in their respective module files.
# ---------------------------------------------------------------------------


def _pluralise(entity_name: str) -> str:
    """Simple English pluralisation sufficient for current entity names."""
    if entity_name.endswith("x"):
        return entity_name + "es"
    if entity_name.endswith("y") and entity_name[-2] not in "aeiou":
        return entity_name[:-1] + "ies"
    return entity_name + "s"


def register_read_toolset(  # noqa: UP047
    mcp: FastMCP[Any],
    entity_name: str,
    list_fn: Callable[..., tuple[Sequence[_T], list[str]]],
    get_fn: Callable[[str], _T | None],
    get_param_name: str,
    not_found_message_template: str,
    tags: set[str],
    extra_list_params: list[str] | None = None,
) -> None:
    """Register a standard list/get tool pair for one entity type.

    Parameters
    ----------
    mcp:
        The FastMCP instance to register tools on.
    entity_name:
        Singular snake_case entity name, e.g. ``"app"``, ``"container"``.
        Tool names are derived as ``modal_list_{plural}`` and
        ``modal_get_{entity_name}``.
    list_fn:
        Callable with signature
        ``(environment_name: str | None = None, **extra) ->
        tuple[Sequence[T], list[str]]``.
        ``extra`` keys are taken from ``extra_list_params``.
    get_fn:
        Callable with signature ``(ref: str) -> T | None``.
    get_param_name:
        The name of the string parameter passed to ``get_fn`` and exposed on
        the generated get tool, e.g. ``"app_ref"``, ``"task_id"``,
        ``"sandbox_ref"``.
    not_found_message_template:
        An f-string-style template with ``{ref}`` as the placeholder, e.g.
        ``"app not found: {ref}"``.
    tags:
        Tag set forwarded to both generated tools, e.g. ``{"apps"}``.
    extra_list_params:
        Optional list of additional parameter names (beyond
        ``environment_name``) accepted by ``list_fn``.  Each extra param is
        exposed on the list tool as ``str | None = None``.
    """
    plural = _pluralise(entity_name)
    list_tool_name = f"modal_list_{plural}"
    get_tool_name = f"modal_get_{entity_name}"
    extra = extra_list_params or []

    # ------------------------------------------------------------------
    # List tool — always accepts environment_name; optional extras are
    # forwarded as str | None keyword arguments.
    # ------------------------------------------------------------------
    if not extra:

        @mcp.tool(
            name=list_tool_name,
            tags=tags,
            annotations=READ_ONLY_ANNOTATIONS,
        )
        def _list_tool(environment_name: str | None = None) -> Any:
            items, warnings = list_fn(environment_name)
            return page_envelope_partial(items, warnings)

    elif len(extra) == 1:
        extra_param = extra[0]
        _build_list_fn_one_extra(mcp, list_tool_name, tags, list_fn, extra_param)

    else:
        raise ValueError(
            f"register_read_toolset supports 0 or 1 extra_list_params; "
            f"got {len(extra)}.  Register the list tool manually."
        )

    # ------------------------------------------------------------------
    # Get tool — accepts a single string ref + optional environment_name.
    # environment_name is accepted for parity with adapter signatures that
    # include it, but not all adapters use it.
    # ------------------------------------------------------------------
    _build_get_fn(
        mcp, get_tool_name, tags, get_fn, get_param_name, not_found_message_template
    )


def _build_list_fn_one_extra(
    mcp: FastMCP[Any],
    list_tool_name: str,
    tags: set[str],
    list_fn: Callable[..., tuple[Sequence[Any], list[str]]],
    extra_param: str,
) -> None:
    """Register the list tool with one extra optional str parameter.

    Uses exec so FastMCP's inspection sees a function with the real parameter
    name (not **kwargs), which is required for correct schema generation.
    """
    _assert_valid_param_name(extra_param)
    fn_src = (
        f"def _list_tool(environment_name: str | None = None,"
        f" {extra_param}: str | None = None) -> ToolEnvelope[Any]:\n"
        f"    items, warnings = list_fn(environment_name,"
        f" **{{{extra_param!r}: {extra_param}}})\n"
        f"    return page_envelope_partial(items, warnings)\n"
    )
    ns: dict[str, Any] = {
        "Any": Any,
        "ToolEnvelope": ToolEnvelope,
        "list_fn": list_fn,
        "page_envelope_partial": page_envelope_partial,
    }
    # Safe: extra_param validated by _assert_valid_param_name() above; fn_src
    # is fully internal (no external input).
    # fmt: off
    exec(fn_src, ns)  # nosemgrep: python.lang.security.audit.exec-detected.exec-detected  # noqa: E501
    # fmt: on
    fn = ns["_list_tool"]
    mcp.tool(name=list_tool_name, tags=tags, annotations=READ_ONLY_ANNOTATIONS)(fn)


def _build_get_fn(
    mcp: FastMCP[Any],
    get_tool_name: str,
    tags: set[str],
    get_fn: Callable[[str], Any],
    get_param_name: str,
    not_found_message_template: str,
) -> None:
    """Register the get tool with a dynamically-named ref parameter."""
    _assert_valid_param_name(get_param_name)
    fn_src = f"""
def _get_tool({get_param_name}: str) -> ToolEnvelope[Any]:
    result = get_fn({get_param_name})
    if result is None:
        return not_found(not_found_message_template.format(ref={get_param_name}))
    return envelope(result)
"""
    ns: dict[str, Any] = {
        "Any": Any,
        "ToolEnvelope": ToolEnvelope,
        "get_fn": get_fn,
        "not_found": not_found,
        "envelope": envelope,
        "not_found_message_template": not_found_message_template,
    }
    # Safe: get_param_name validated by _assert_valid_param_name() above; fn_src
    # is fully internal (no external input).
    # fmt: off
    exec(fn_src, ns)  # nosemgrep: python.lang.security.audit.exec-detected.exec-detected  # noqa: E501
    # fmt: on
    fn = ns["_get_tool"]
    mcp.tool(name=get_tool_name, tags=tags, annotations=READ_ONLY_ANNOTATIONS)(fn)


__all__ = [
    "MUTATING_ANNOTATIONS",
    "READ_ONLY_ANNOTATIONS",
    "REQUEST_ID",
    "disabled_error",
    "envelope",
    "not_found",
    "page_envelope",
    "page_envelope_partial",
    "register_read_toolset",
]
