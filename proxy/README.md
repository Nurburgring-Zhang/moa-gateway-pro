# MoA Gateway Pro - Go Proxy Layer

High-performance reverse proxy (Go) sitting in front of the Python MoA Gateway.

## Build

```bash
cd proxy/
go build -o moa-proxy .
```

## Run

```bash
./moa-proxy --listen :8080 --backend http://127.0.0.1:8000
```

## Docker

```bash
docker build -t moa-proxy .
docker run -p 8080:8080 -e MOA_JWT_SECRET=your-secret moa-proxy
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| MOA_JWT_SECRET | (empty) | JWT signing secret for auth |
| PROXY_RATE_LIMIT | 1000 | Max requests/sec per IP |
| PROXY_MAX_CONN | 500 | Connection pool size |
| PROXY_READ_TIMEOUT | 30 | Read timeout (seconds) |
| PROXY_WRITE_TIMEOUT | 120 | Write timeout (seconds) |
| PROXY_METRICS | true | Enable /metrics endpoint |
| PROXY_AUTH | true | Enable JWT auth check |

## Architecture

```
Client -> [Go Proxy :8080] -> [Python Backend :8000]
              |
              +-- JWT validation (fast path)
              +-- Rate limiting (token bucket)
              +-- SSE stream relay (zero-buffer)
              +-- Prometheus metrics
              +-- Health check (backend probe)
```

## Performance Targets

- P50 latency: <100µs (proxy overhead only)
- Throughput: >5000 RPS per core
- Memory: <50MB baseline
