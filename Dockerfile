# Single-image deployment: the web UI and the scheduled pipeline are the same app.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOFRADAR_DATA_DIR=/data

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[pdf,images]"

COPY config ./config

RUN mkdir -p /data && useradd --create-home --uid 10001 hofradar && chown -R hofradar /data /app
USER hofradar

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["sh", "-c", "hofradar init-db && hofradar serve --host 0.0.0.0 --port ${PORT:-8000}"]
