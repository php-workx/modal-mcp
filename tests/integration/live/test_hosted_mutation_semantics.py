"""Hosted mutating live semantics verification scaffold.

These tests are opt-in. Default runs skip, fake mode stays local and
non-evidence, and real non-prod mode still fails fast because execution is not
implemented yet.
"""

from __future__ import annotations

import os

import pytest

_MODE_ENV = "MODAL_MCP_MUTATING_SEMANTICS_MODE"
_RESOURCE_ENV_VARS = (
    "MODAL_MCP_MUTATING_APP_ID",
    "MODAL_MCP_MUTATING_CONTAINER_ID",
    "MODAL_MCP_MUTATING_SANDBOX_ID",
)
_BEHAVIOR_FIELDS = (
    "in_flight_work",
    "rollback_handling",
    "sigint_reassignment",
    "irreversibility",
    "dry_run_impact",
)
_BEHAVIOR_MATRIX = {
    "modal_stop_app": {
        "in_flight_work": (
            "Scaffolded assertion only; fake mode records the in-flight note "
            "without stopping an app."
        ),
        "rollback_handling": (
            "Scaffolded assertion only; fake mode records the rollback note "
            "without issuing rollback."
        ),
        "sigint_reassignment": (
            "Scaffolded assertion only; fake mode records the reassignment "
            "note without simulating interruption."
        ),
        "irreversibility": (
            "Scaffolded assertion only; fake mode records the irreversibility "
            "note without executing a stop transition."
        ),
        "dry_run_impact": (
            "Fake mode keeps this as non-evidence; no Modal mutation call is executed."
        ),
    },
    "modal_rollback_app": {
        "in_flight_work": (
            "Scaffolded assertion only; fake mode records the in-flight note "
            "without rolling back an app."
        ),
        "rollback_handling": (
            "Scaffolded assertion only; fake mode records the rollback note "
            "without restoring a checkpoint."
        ),
        "sigint_reassignment": (
            "Scaffolded assertion only; fake mode records the reassignment "
            "note without simulating interruption."
        ),
        "irreversibility": (
            "Scaffolded assertion only; fake mode records the irreversibility "
            "note without executing a reverse transition."
        ),
        "dry_run_impact": (
            "Fake mode keeps this as non-evidence; no Modal mutation call is executed."
        ),
    },
    "modal_stop_container": {
        "in_flight_work": (
            "Scaffolded assertion only; fake mode records the in-flight note "
            "without stopping a container."
        ),
        "rollback_handling": (
            "Scaffolded assertion only; fake mode records the rollback note "
            "without restoring container state."
        ),
        "sigint_reassignment": (
            "Scaffolded assertion only; fake mode records the reassignment "
            "note without simulating interruption."
        ),
        "irreversibility": (
            "Scaffolded assertion only; fake mode records the irreversibility "
            "note without executing a stop transition."
        ),
        "dry_run_impact": (
            "Fake mode keeps this as non-evidence; no Modal mutation call is executed."
        ),
    },
    "modal_terminate_sandbox": {
        "in_flight_work": (
            "Scaffolded assertion only; fake mode records the in-flight note "
            "without terminating a sandbox."
        ),
        "rollback_handling": (
            "Scaffolded assertion only; fake mode records the rollback note "
            "without restoring a sandbox."
        ),
        "sigint_reassignment": (
            "Scaffolded assertion only; fake mode records the reassignment "
            "note without simulating interruption."
        ),
        "irreversibility": (
            "Scaffolded assertion only; fake mode records the irreversibility "
            "note without executing a termination."
        ),
        "dry_run_impact": (
            "Fake mode keeps this as non-evidence; no Modal mutation call is executed."
        ),
    },
}
_HOSTED_MUTATING_TOOLS = tuple(_BEHAVIOR_MATRIX)


def _scaffold_enabled() -> bool:
    return (
        os.environ.get("MODAL_MCP_LIVE") == "1"
        and os.environ.get("MODAL_MCP_MUTATING_SEMANTICS") == "1"
    )


def _execution_mode() -> str:
    if not _scaffold_enabled():
        return "skip"
    return os.environ.get(_MODE_ENV, "nonprod").strip().lower() or "nonprod"


def _require_nonprod_resource_ids() -> dict[str, str]:
    missing = [name for name in _RESOURCE_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise pytest.UsageError(
            "Hosted mutating semantics opt-in needs explicit non-prod resource "
            f"identifiers: {', '.join(_RESOURCE_ENV_VARS)}. "
            "Use MODAL_MCP_MUTATING_SEMANTICS_MODE=fake for local "
            "evidence-free assertions."
        )
    return {name: os.environ[name] for name in _RESOURCE_ENV_VARS}


pytestmark = [
    pytest.mark.hosted_mutating_live,
    pytest.mark.skipif(
        not _scaffold_enabled(),
        reason=(
            "Set MODAL_MCP_LIVE=1 and MODAL_MCP_MUTATING_SEMANTICS=1 "
            "to run hosted mutating semantics checks."
        ),
    ),
]


@pytest.fixture(scope="module", autouse=True)
def _guard_nonprod_mode() -> None:
    mode = _execution_mode()
    if mode == "skip" or mode == "fake":
        return

    _require_nonprod_resource_ids()
    raise pytest.UsageError(
        "Real hosted mutation execution is not implemented yet in this scaffold. "
        "Use MODAL_MCP_MUTATING_SEMANTICS_MODE=fake for local assertions."
    )


@pytest.mark.parametrize("tool_name", _HOSTED_MUTATING_TOOLS)
def test_hosted_mutation_tool_semantics_scaffold(tool_name: str) -> None:
    """Fake mode asserts the harness contract without calling Modal."""

    if _execution_mode() != "fake":
        pytest.skip("Set MODAL_MCP_MUTATING_SEMANTICS_MODE=fake for local assertions.")

    behavior = _BEHAVIOR_MATRIX[tool_name]
    assert set(behavior) == set(_BEHAVIOR_FIELDS)
    assert "scaffolded assertion only" in behavior["in_flight_work"].lower()
    assert "scaffolded assertion only" in behavior["rollback_handling"].lower()
    assert "scaffolded assertion only" in behavior["sigint_reassignment"].lower()
    assert "scaffolded assertion only" in behavior["irreversibility"].lower()
    assert "non-evidence" in behavior["dry_run_impact"].lower()
