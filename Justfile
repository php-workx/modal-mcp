set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# modal-mcp project quality gate
# Single source of truth for local checks; hooks and CI should delegate here.

sources := "src tests scripts"

default:
    @just --list

# --- Quality gates ---

# Fast pre-commit gate: formatting, linting, typing, schema drift, and fast tests.
pre-commit: fmt lint type-check schema-check test-fast

# Pre-push gate: pre-commit checks plus full tests and local security scans.
pre-push: pre-commit test security

# Local quality gate without release/container jobs.
check-local: pre-push actionlint shellcheck

# Alias for the full local quality gate.
check: check-local

# Developer shorthand.
dev: check-local
    @echo "All local checks completed."

# Language CI gate used by humans, hooks, and CI when validating Python-only changes.
langci: fmt lint type-check schema-check test

# Python CI gate with coverage reporting.
ci-python: fmt lint type-check schema-check coverage

# Security CI gate for checks that run through the Python toolchain.
ci-security: vuln semgrep

# CI gate for repository automation files.
ci-workflow: actionlint shellcheck

# --- Formatting and static analysis ---

# Check Python formatting without writing changes.
fmt:
    uv run ruff format --check {{sources}}

# Format Python files in-place.
format:
    uv run ruff format {{sources}}

# Run Ruff lint checks.
lint:
    uv run ruff check .

# Auto-fix Ruff lint issues where safe.
lint-fix:
    uv run ruff check . --fix

# Auto-fix formatting and safe lint issues, then verify.
autofix: format lint-fix lint

# Strict type check.
type-check:
    uv run mypy --strict src

# Lint GitHub Actions workflows.
actionlint:
    @command -v actionlint >/dev/null 2>&1 || { echo "error: actionlint is required for this gate" >&2; exit 127; }
    @if [ -d .github/workflows ]; then \
        find .github/workflows -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) -print0 | xargs -0 -r actionlint; \
    fi

# Lint shell hook files.
shellcheck:
    @command -v shellcheck >/dev/null 2>&1 || { echo "error: shellcheck is required for this gate" >&2; exit 127; }
    @hook_files="$(find .githooks -maxdepth 1 -type f -print 2>/dev/null)"; \
        if [ -n "$hook_files" ]; then \
            find .githooks -maxdepth 1 -type f -print0 | xargs -0 shellcheck; \
        fi

# --- Tests and contracts ---

# Run the fast suite used by the pre-commit hook.
test-fast:
    uv run pytest -q tests/unit tests/contract tests/test_import.py

# Run all non-live tests. Live Modal tests self-skip unless MODAL_MCP_LIVE=1.
test:
    uv run pytest -q

# Run contract tests only.
test-contract:
    uv run pytest -q tests/contract

# Run the optional live Modal integration smoke tests.
test-live:
    MODAL_MCP_LIVE=1 uv run pytest -q tests/integration/live

# Generate a terminal and XML coverage report.
coverage:
    uv run pytest -q --cov=modal_mcp --cov-report=term-missing --cov-report=xml

# Verify MCP schema snapshots are current.
schema-check:
    uv run python scripts/generate_schemas.py --check

# Regenerate MCP schema snapshots.
schema-update:
    uv run python scripts/generate_schemas.py

# --- Security ---

# Security gate: dependency vulnerabilities, local secret scan, and SAST.
security: vuln betterleaks semgrep

# Scan locked Python dependencies for known vulnerabilities.
vuln:
    #!/usr/bin/env bash
    set -euo pipefail
    audit_requirements="$(mktemp)"
    trap 'rm -f "$audit_requirements"' EXIT
    uv export --frozen --extra dev --no-emit-project --format requirements-txt > "$audit_requirements"
    uv run pip-audit --strict --disable-pip -r "$audit_requirements"

# Local alternative dependency audit against uv.lock.
uv-audit:
    uv audit --preview-features audit --frozen

# Scan git history for leaked secrets locally. CI uses TruffleHog.
betterleaks:
    @command -v betterleaks >/dev/null 2>&1 || { echo "error: betterleaks is required for this gate" >&2; exit 127; }
    betterleaks git --no-banner

# Run Semgrep SAST.
semgrep:
    @command -v semgrep >/dev/null 2>&1 || { echo "error: semgrep is required for this gate" >&2; exit 127; }
    semgrep scan --config auto --error .

# --- Packaging ---

# Build source and wheel distributions.
build:
    uv build

# --- Setup ---

# Install/sync development dependencies.
install-dev:
    uv sync --extra dev

# Configure this worktree to use the checked-in git hooks.
hooks-install:
    git config core.hooksPath .githooks
    @echo "Git hooks configured (.githooks/)"

# Install pre-commit framework hooks for users who prefer the pre-commit runner.
pre-commit-install:
    uv run pre-commit install --hook-type pre-commit --hook-type pre-push

# Run pre-commit hooks against the full tree.
pre-commit-run:
    uv run pre-commit run --all-files

# Full developer setup.
setup: install-dev hooks-install
    @echo "Development environment ready. Run: just check-local"

# Remove build artifacts and local caches.
clean:
    rm -rf dist/ build/ *.egg-info
    rm -rf .coverage coverage.xml htmlcov .pytest_cache .ruff_cache .mypy_cache
    find . -type d -name __pycache__ -prune -exec rm -rf {} +
    find . -type f -name '*.pyc' -delete
