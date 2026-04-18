# mm-9aeo: Hosted Mutating Semantics Live Harness Notes

**Version:** `mm-9aeo-v1.0.0-scaffold-only`  
**Status:** scaffold-only, non-production, non-evidence  

## Marker and run command

- Pytest marker: `hosted_mutating_live`
- Required opt-in env vars: `MODAL_MCP_LIVE=1`, `MODAL_MCP_MUTATING_SEMANTICS=1`
- Fake mode run: `MODAL_MCP_LIVE=1 MODAL_MCP_MUTATING_SEMANTICS=1 MODAL_MCP_MUTATING_SEMANTICS_MODE=fake uv run pytest -q tests/integration/live/test_hosted_mutation_semantics.py -m hosted_mutating_live`
- Non-prod execution attempts must also provide explicit resource identifiers; if
  they are missing, the harness must raise a setup error instead of skipping.

## Non-productivity impact note

Dry-run (`pytest` default config) must never execute these workflows. All tests remain
skipped unless both opt-in variables are present. Fake mode is the only
evidence-free execution path in this ticket. Real non-prod execution is still
scaffold-only and should fail fast if required identifiers are absent.

## Cross-link

See the full verification field plan in `[live-modal-semantics.md](../live-modal-semantics.md)`.
