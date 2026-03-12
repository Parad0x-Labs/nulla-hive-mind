FROM python:3.12-slim AS base

LABEL maintainer="NULLA Team"
LABEL description="Decentralized NULLA Agent Node"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV NULLA_DATA_DIR=/data

RUN mkdir -p /data

EXPOSE 49152/udp
EXPOSE 8765

# Default: run the agent
CMD ["python3", "apps/nulla_agent.py"]
