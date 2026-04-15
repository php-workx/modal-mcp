# syntax=docker/dockerfile:1.7

# TODO(mm-qjp0): replace this tag with the selected digest-pinned base before
# release once the release pipeline owns base-image refreshes.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system modal-mcp \
    && adduser --system --ingroup modal-mcp --home /app modal-mcp

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN pip install uv \
    && uv sync --frozen --no-dev

USER modal-mcp
EXPOSE 8765

CMD ["/app/.venv/bin/modal-mcp"]
