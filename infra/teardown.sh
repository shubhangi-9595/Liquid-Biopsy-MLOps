#!/usr/bin/env bash
# Delete everything this project created in Azure, so nothing keeps costing money.
# Also purges the soft-deleted Key Vaults and Azure ML workspace, so `setup.sh` can be
# run again later with the same names.

set -euo pipefail
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-liquid-biopsy}"
APP_REG_NAME="gh-liquid-biopsy-deploy"

read -r -p "Permanently delete resource group '$RESOURCE_GROUP' and everything in it? [y/N] " answer
[[ "$answer" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

az extension add --name ml --upgrade --only-show-errors -o none || echo "Using the preinstalled Azure CLI extension"
ML_WS=$(az ml workspace list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv 2>/dev/null || true)
if [ -n "$ML_WS" ]; then
  echo "==> Permanently deleting Azure ML workspace $ML_WS"
  az ml workspace delete -g "$RESOURCE_GROUP" -n "$ML_WS" --permanently-delete --yes || true
fi

KEY_VAULTS=$(az keyvault list -g "$RESOURCE_GROUP" --query "[].name" -o tsv 2>/dev/null || true)

echo "==> Deleting resource group (several minutes)"
az group delete --name "$RESOURCE_GROUP" --yes

for kv in $KEY_VAULTS; do
  echo "==> Purging soft-deleted Key Vault $kv"
  az keyvault purge --name "$kv" || true
done

CLIENT_ID=$(az ad app list --display-name "$APP_REG_NAME" --query "[0].appId" -o tsv 2>/dev/null || true)
if [ -n "$CLIENT_ID" ]; then
  echo "==> Deleting GitHub Actions app registration"
  az ad app delete --id "$CLIENT_ID"
fi

echo "Done. Nothing from this project is left in Azure."
