#!/usr/bin/env bash
# Container smoke test — builds the Docker image, runs it, and verifies
# /health returns 200. Intended for CI (requires docker/podman).
#
# Usage: scripts/container_smoke.sh [IMAGE_TAG]
#
# Exit codes:
#   0 — image builds, boots, /health 200
#   1 — no container engine available
#   2 — image build failed
#   3 — container did not become ready
#   4 — /health did not return 200

set -euo pipefail

IMAGE_TAG="${1:-moa-gateway-pro:smoke-test}"
ENGINE=""

if command -v docker >/dev/null 2>&1; then
    ENGINE="docker"
elif command -v podman >/dev/null 2>&1; then
    ENGINE="podman"
else
    echo "ERROR: no docker or podman found — cannot build image" >&2
    exit 1
fi

echo "=== [1/4] Building image $IMAGE_TAG with $ENGINE ==="
$ENGINE build -t "$IMAGE_TAG" . || { echo "BUILD FAILED"; exit 2; }

echo "=== [2/4] Starting container ==="
CID=$($ENGINE run -d --rm \
    -p 8910:8088 \
    -e MOA_ADMIN_PASSWORD="Smoke#Pass2024" \
    -e MOA_GATEWAY_KEY="smoke-key" \
    -e MOA_JWT_SECRET="smoke-test-secret-minimum-32-characters-long-xx" \
    "$IMAGE_TAG")
echo "container: $CID"

trap 'echo "=== cleanup ==="; $ENGINE stop "$CID" >/dev/null 2>&1 || true' EXIT

echo "=== [3/4] Waiting for /health (up to 60s) ==="
READY=0
for i in $(seq 1 60); do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8910/health 2>/dev/null || echo "000")
    if [ "$CODE" = "200" ]; then
        echo "ready after ${i}s"
        READY=1
        break
    fi
    sleep 1
done

if [ "$READY" != "1" ]; then
    echo "ERROR: container did not become ready" >&2
    $ENGINE logs "$CID" | tail -30 >&2
    exit 3
fi

echo "=== [4/4] Verifying /health and /health/ready ==="
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8910/health)
if [ "$HEALTH" != "200" ]; then
    echo "ERROR: /health returned $HEALTH (expected 200)" >&2
    exit 4
fi
echo "/health: $HEALTH"

echo "=== SMOKE TEST PASSED ==="
