# Contributing

## Governance posture

This project uses Apache-2.0 licensing with a DCO contribution model.
There is no CLA. Contributions are accepted through pull requests and
must be attributable to the person submitting them.

## Required sign-off

Every contribution must include a `Signed-off-by` line in the commit
message. The lowercase `signed-off-by` token must be present in the
final commit, for example:

```text
Signed-off-by: Your Name <you@example.com>
```

This is the standard Developer Certificate of Origin (`DCO`) signal. If
your commits are squashed or rebased, make sure the final commit still
contains the sign-off.

## Release expectation

Maintainers are expected to publish signed release tags and validate the
release through CI before distribution. Do not assume an unsigned tag or
artifact is a valid release.

## Practical rules

- keep changes focused
- include tests or verification where behavior changes
- avoid unrelated refactors in the same PR
- describe user-facing impact clearly in the PR body

## Local quality gates

Install the local command runners before using the validation targets:

```bash
brew install just shellcheck actionlint gitleaks semgrep
```

On Linux, install the same tools with your package manager or the upstream
release packages.

Install the local tools and git hooks once per worktree:

```bash
just setup
```

The checked-in hooks delegate to the same `just` targets used by maintainers:

```bash
just pre-commit   # format, lint, type check, schema drift, fast tests
just pre-push     # pre-commit gate, full tests, vulnerability/security scans
just check-local  # pre-push gate plus workflow and hook linting
```

Use targeted commands while iterating:

```bash
just format
just lint
just type-check
just test-fast
just vuln
just semgrep
```
