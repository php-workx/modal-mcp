# syntax=docker/dockerfile:1.7

FROM python:3.12-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286 AS runtime

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
