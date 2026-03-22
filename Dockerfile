FROM python:3.12-slim AS base

LABEL maintainer="Parad0x Labs"
LABEL description="NULLA Hive Mind — local-first decentralized AI agent"
LABEL org.opencontainers.image.source="https://github.com/Parad0x-Labs/nulla-hive-mind"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV NULLA_DATA_DIR=/data

RUN mkdir -p /data

EXPOSE 49152/udp
EXPOSE 8765
EXPOSE 11435

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:11435/healthz || exit 1

# Default: run the agent API server
CMD ["python3", "-m", "apps.nulla_api_server"]
