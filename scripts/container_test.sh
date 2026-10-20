#!/usr/bin/env bash
# Test the built image the way production runs it: API + Postgres in containers,
# migrations on startup, a prediction stored, then the drift job from the same image.
#
#   bash scripts/container_test.sh liquid-biopsy-api:ci

set -euo pipefail
IMAGE="${1:?Usage: container_test.sh IMAGE}"
NET=lb-test-net
DB_URL="postgresql://lb:lbpass@lb-postgres:5432/liquidbiopsy"

cleanup() {
  echo "--- API logs"; docker logs lb-api 2>&1 | tail -40 || true
  docker rm -f lb-api lb-postgres >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$NET" >/dev/null
docker run -d --name lb-postgres --network "$NET" \
  -e POSTGRES_USER=lb -e POSTGRES_PASSWORD=lbpass -e POSTGRES_DB=liquidbiopsy postgres:16-alpine >/dev/null
docker run -d --name lb-api --network "$NET" -p 8000:8000 -e DATABASE_URL="$DB_URL" "$IMAGE" >/dev/null

bash "$(dirname "$0")/smoke_test.sh" http://localhost:8000 --expect-db

echo "--- Drift job (same image, different command)"
docker run --rm --network "$NET" -e DATABASE_URL="$DB_URL" -e DRIFT_MIN_PREDICTIONS=1 "$IMAGE" \
  python -m app.drift 2>&1 | tee drift.log
grep -qE "drift_check|DRIFT_DETECTED" drift.log || { echo "FAILED: drift job produced no report"; exit 1; }
curl -sf http://localhost:8000/drift/latest >/dev/null || { echo "FAILED: /drift/latest"; exit 1; }
echo "Container test PASSED"
