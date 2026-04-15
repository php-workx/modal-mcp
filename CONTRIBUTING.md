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
