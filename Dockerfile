FROM ghcr.io/astral-sh/uv:python3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --compile-bytecode

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml ./
COPY src/ ./src/
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "dmo.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
