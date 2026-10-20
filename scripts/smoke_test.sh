#!/usr/bin/env bash
# Smoke-test a running API: the same checks for Docker, kind, Container Apps and AKS.
#
#   bash scripts/smoke_test.sh http://localhost:8000            # API only
#   bash scripts/smoke_test.sh https://<app>.azurecontainerapps.io --expect-db
#
# Waits for the service (cold starts, scale-from-zero), then checks health, a prediction
# and, with --expect-db, that the prediction was stored and is visible in monitoring.

set -euo pipefail

BASE_URL="${1:?Usage: smoke_test.sh BASE_URL [--expect-db]}"
EXPECT_DB="${2:-}"
REQUEST="$(cd "$(dirname "$0")/.." && pwd)/examples/sample_request.json"
TRIES="${SMOKE_TRIES:-40}"

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "Waiting for $BASE_URL/health ..."
for i in $(seq 1 "$TRIES"); do
  if HEALTH=$(curl -sf --max-time 10 "$BASE_URL/health"); then
    if [ "$(echo "$HEALTH" | json 'd["model_loaded"]')" = "True" ]; then
      if [ "$EXPECT_DB" != "--expect-db" ] || [ "$(echo "$HEALTH" | json 'd["database"]')" = "connected" ]; then
        break
      fi
    fi
  fi
  [ "$i" = "$TRIES" ] && { echo "FAILED: service not healthy. Last response: ${HEALTH:-none}"; exit 1; }
  sleep 5
done
echo "health:     $HEALTH"

PRED=$(curl -sf --max-time 30 -X POST "$BASE_URL/predict" -H "Content-Type: application/json" --data @"$REQUEST")
echo "predict:    $(echo "$PRED" | json '[(p["label_name"], p["cancer_probability"]) for p in d["predictions"]]')"
[ "$(echo "$PRED" | json 'len(d["predictions"])')" = "2" ] || { echo "FAILED: expected 2 predictions"; exit 1; }
[ "$(echo "$PRED" | json 'd["predictions"][0]["label_name"]')" = "healthy" ] || { echo "FAILED: healthy sample misclassified"; exit 1; }
[ "$(echo "$PRED" | json 'd["predictions"][1]["label_name"]')" = "cancer" ] || { echo "FAILED: cancer sample misclassified"; exit 1; }

if [ "$EXPECT_DB" = "--expect-db" ]; then
  [ "$(echo "$PRED" | json 'd["predictions"][0]["prediction_id"] is not None')" = "True" ] \
    || { echo "FAILED: prediction was not stored in the database"; exit 1; }
  SUMMARY=$(curl -sf --max-time 10 "$BASE_URL/monitoring/summary")
  echo "monitoring: $(echo "$SUMMARY" | json '[(m["model_version"], m["n_predictions"]) for m in d]')"
  [ "$(echo "$SUMMARY" | json 'd[0]["n_predictions"] >= 2')" = "True" ] || { echo "FAILED: monitoring summary"; exit 1; }
fi

echo "Smoke test PASSED"
