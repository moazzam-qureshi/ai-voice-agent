# syntax=docker/dockerfile:1.7

# === Builder ===
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


# === Runtime ===
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    # Worker imports shared.tasks which reuses api search code, so both
    # api and worker source trees must be on PYTHONPATH.
    PYTHONPATH="/app:/app/services/api/src:/app/services/worker/src"

# Runtime system deps: curl + WeasyPrint runtime libs (PDF gen lives here).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-inter \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

# Pre-fetch the embedding model so the first knowledge-doc ingest doesn't
# pay the cold-start download cost.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY pyproject.toml uv.lock ./
COPY shared /app/shared
COPY services/api /app/services/api
COPY services/worker /app/services/worker

# Default: run the Dramatiq worker. The scheduler service overrides this
# via `command:` in docker-compose.yml.
CMD ["dramatiq", "worker.main", "--processes", "1", "--threads", "2"]
