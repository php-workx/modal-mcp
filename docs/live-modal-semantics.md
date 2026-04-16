# Hosted Mutating Live Semantics Verification Plan

**Document ID:** mm-9aeo  
**Version:** `mm-9aeo-v1.0.0-scaffold-only`  
**Date:** 2026-04-16  
**Scope:** `modal_stop_app`, `modal_rollback_app`, `modal_stop_container`, `modal_terminate_sandbox`  
**Execution posture:** non-production, explicit manual opt-in only

## 1) Purpose

This document defines the proof plan for hosted mutating live tool semantics without
enabling those mutation calls. The current harness is scaffold-only and
non-evidence; real non-production execution remains a future ticket in
`mm-9aeo` and does not exist yet.

## 2) Evidence status

- Verified evidence: none. This document is a scaffold, not a result.
- Scaffolded assertions: the fake mode test harness can validate the behavior
  matrix and the documentation contract without calling Modal mutation APIs.
- Planned execution: non-prod mutation execution remains gated by explicit
  resource identifiers and is not yet enabled.

## 3) Non-prod gating

- Custom pytest marker: `hosted_mutating_live`
- Required environment flags:
  - `MODAL_MCP_LIVE=1`
  - `MODAL_MCP_MUTATING_SEMANTICS=1`
- Harness mode:
  - `MODAL_MCP_MUTATING_SEMANTICS_MODE=fake` runs local behavior-field
    assertions only.
  - Any other mode is treated as a non-prod execution attempt and requires
    explicit resource identifiers.
  - Missing resource identifiers must fail with a setup error, not a silent
    skip.
- Scope:
  - Checks are opt-in.
  - Normal CI and local default runs skip these tests.
  - Harness files exist in `tests/integration/live`.

## 4) Behavior matrix (scaffolded, not evidence)

| Tool | In-flight work behavior | Rollback handling | SIGINT/reassignment | Irreversibility | Dry-run impact |
| --- | --- | --- | --- | --- | --- |
| `modal_stop_app` | Scaffolded assertion only. Local mode records the intended in-flight behavior field; no app stop is called. | Scaffolded assertion only. Local mode records the rollback note; no rollback is issued. | Scaffolded assertion only. Local mode records the reassignment note; no interrupt is simulated. | Scaffolded assertion only. Local mode records the irreversibility note; no transition is executed. | Fake mode keeps this non-evidence: no remote state changes, no Modal mutation call. |
| `modal_rollback_app` | Scaffolded assertion only. Local mode records the intended in-flight behavior field; no app rollback is called. | Scaffolded assertion only. Local mode records the rollback note; no remote checkpoint is restored. | Scaffolded assertion only. Local mode records the reassignment note; no interrupt is simulated. | Scaffolded assertion only. Local mode records the irreversibility note; no reverse transition is executed. | Fake mode keeps this non-evidence: no remote state changes, no Modal mutation call. |
| `modal_stop_container` | Scaffolded assertion only. Local mode records the intended in-flight behavior field; no container stop is called. | Scaffolded assertion only. Local mode records the rollback note; no container state is restored. | Scaffolded assertion only. Local mode records the reassignment note; no interrupt is simulated. | Scaffolded assertion only. Local mode records the irreversibility note; no stop transition is executed. | Fake mode keeps this non-evidence: no remote state changes, no Modal mutation call. |
| `modal_terminate_sandbox` | Scaffolded assertion only. Local mode records the intended in-flight behavior field; no sandbox termination is called. | Scaffolded assertion only. Local mode records the rollback note; no reverse transition is restored. | Scaffolded assertion only. Local mode records the reassignment note; no interrupt is simulated. | Scaffolded assertion only. Local mode records the irreversibility note; no termination is executed. | Fake mode keeps this non-evidence: no remote state changes, no Modal mutation call. |

## 5) Checklist and test matrix

- [x] Add scoped harness marker and skip-by-default gating.
- [x] Add a dedicated `hosted_mutating_live` test set under `tests/integration/live`.
- [x] Add fake mode that exercises behavior-field assertions without Modal mutations.
- [ ] Keep the real non-prod execution contract open until the adapter path
  exists; explicit resource identifiers are only for the future execution path.
- [ ] Capture verified evidence for each behavior field above and link
  artifacts in run notes.
- [ ] Confirm no mutating code path is enabled while status is `scaffold`.

| Case | Expected evidence | Dry-run status | Manual proof requirement |
| --- | --- | --- | --- |
| In-flight work behavior | Behavior-field assertion in fake mode; remote logs or traces only after real execution is enabled. | Scaffolded | Required for real execution |
| Rollback handling | Behavior-field assertion in fake mode; rollback traces only after real execution is enabled. | Scaffolded | Required for real execution |
| SIGINT/reassignment | Behavior-field assertion in fake mode; interruption traces only after real execution is enabled. | Scaffolded | Required for real execution |
| Irreversibility | Behavior-field assertion in fake mode; irreversibility proof only after real execution is enabled. | Scaffolded | Required for real execution |

## 6) Safe execution statement

This document defines scaffold-only, non-prod live verification. It does **not**
enable hosted mutating tools. Fake mode is the only executable path in this
ticket. Non-prod execution attempts without explicit resource identifiers must
fail fast with a setup error instead of quietly skipping.

Reference harness: `tests/integration/live/test_hosted_mutation_semantics.py`.
