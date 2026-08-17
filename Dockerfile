FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim

LABEL maintainer="MoA Gateway Pro Team"
LABEL description="Industrial-grade Multi-Model Orchestration Gateway"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY moa_gateway/ ./moa_gateway/
COPY pyproject.toml ./
COPY config.yaml ./

ENV PYTHONPATH=/app
ENV PYTHONIOENCODING=utf-8
ENV PYTHONDONTWRITEBYTECODE=1
ENV MOA_HOST=0.0.0.0
ENV MOA_PORT=8088

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8088/health || exit 1

RUN useradd -r -s /bin/false moa && mkdir -p /app/data && chown -R moa:moa /app
USER moa

CMD ["python", "-m", "uvicorn", "moa_gateway.server:app", \
     "--host", "0.0.0.0", "--port", "8088", \
     "--workers", "1", "--log-level", "info"]
