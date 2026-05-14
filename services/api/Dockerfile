# syntax=docker/dockerfile:1.7

# === Builder ===
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    # Some transitive deps (hf-xet, zopfli, torch CPU wheels) are large
    # enough that the default 30s wheel-download timeout flakes on slow
    # links. 5 min is plenty even on a constrained connection.
    UV_HTTP_TIMEOUT=300

# Build deps for native wheels.
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
    PYTHONPATH="/app:/app/services/api/src"

# Runtime system deps:
# - curl: only for ad-hoc debugging (no container HEALTHCHECK, see CLAUDE.md)
# - WeasyPrint deps: libpango, libcairo, libgdk-pixbuf, shared-mime-info,
#   fontconfig + Inter font for the PDF rendering. We avoid full ghostscript.
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

# Pre-fetch the sentence-transformers embedding model into the image so the
# first /agent/search call doesn't pay a ~90MB HuggingFace download.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Source layout: pyproject + workspace dirs.
COPY pyproject.toml uv.lock alembic.ini ./
COPY alembic /app/alembic
COPY shared /app/shared
COPY services/api /app/services/api

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
