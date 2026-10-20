#!/usr/bin/env bash
# Deploy from Azure Cloud Shell without GitHub Actions, e.g. if your university blocks the
# Entra ID app registration that CD needs. CI on GitHub still lints, tests and trains.
#
# 1. Get a trained model into models/:
#      - download the "model" artifact from a green CI run and unzip it into models/, or
#      - pip install -r requirements-train.txt && PYTHONPATH=src python -m liquid_biopsy.train
# 2. bash infra/deploy_manual.sh
#
# The image is built inside Azure Container Registry (az acr build), so Docker is not needed.

set -euo pipefail
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-liquid-biopsy}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

for f in model.joblib metadata.json reference_stats.json; do
  [ -f "models/$f" ] || { echo "models/$f is missing: train first (see the top of this script)"; exit 1; }
done

ACR=$(az acr list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)
TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)"
IMAGE="$ACR.azurecr.io/liquid-biopsy-api:$TAG"

echo "==> Building $IMAGE in Azure Container Registry"
az acr build --registry "$ACR" --image "liquid-biopsy-api:$TAG" --image "liquid-biopsy-api:latest" .

echo "==> Deploying the Container App and drift job (Bicep)"
URL=$(az deployment group create -g "$RESOURCE_GROUP" -n "app-$TAG" \
  --template-file infra/bicep/app.bicep -p image="$IMAGE" \
  --query properties.outputs.url.value -o tsv)

echo "Deployed. Open $URL/docs"
