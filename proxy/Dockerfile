# Multi-stage build for minimal production image
FROM golang:1.22-alpine AS builder

WORKDIR /app
COPY go.mod go.sum* ./
RUN go mod download 2>/dev/null || true
COPY *.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o moa-proxy .

FROM alpine:3.19
RUN apk --no-cache add ca-certificates tzdata
WORKDIR /app
COPY --from=builder /app/moa-proxy .

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s \
    CMD wget -qO- http://localhost:8080/health || exit 1

ENTRYPOINT ["./moa-proxy"]
CMD ["--listen", ":8080", "--backend", "http://host.docker.internal:8000"]
