FROM python:3.11-slim

LABEL maintainer="MoA Gateway Pro Team"
LABEL description="Industrial-grade Multi-Model Orchestration Gateway - v1.7.3"
LABEL version="1.7.3"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY moa_gateway/ ./moa_gateway/
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY start.py ./
COPY start.sh ./
COPY config.yaml ./
COPY pyproject.toml ./
COPY README.md ./
COPY CHANGELOG.md ./

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONIOENCODING=utf-8
ENV MOA_HOST=0.0.0.0
ENV MOA_PORT=8088
ENV MOA_LOG_LEVEL=info

# Expose ports
EXPOSE 8088

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8088/health || exit 1

# Default command
CMD ["python", "-m", "uvicorn", "moa_gateway.server:app", \
     "--host", "0.0.0.0", "--port", "8088", \
     "--log-level", "info"]
