#!/usr/bin/env bash
# One-time Azure setup. Run in Azure Cloud Shell (Bash) or anywhere with Azure CLI after `az login`:
#
#   GITHUB_REPO=<github-user>/liquid-biopsy-mlops bash infra/setup.sh
#
# Optional: LOCATION (default westeurope), POSTGRES_LOCATION, RESOURCE_GROUP
#
# What it does
#   1. checks your subscription's allowed regions (student subscriptions are limited)
#   2. registers the Azure resource providers the project uses
#   3. creates an Entra ID app with a federated credential, so GitHub Actions can log in
#      to Azure with OIDC: no password or key is ever stored in GitHub
#   4. deploys infra/bicep/main.bicep (registry, Key Vault, PostgreSQL, Azure ML,
#      Application Insights, Container Apps environment, role assignments)
#   5. configures the GitHub repo (if the GitHub CLI is logged in) or prints what to add
#
# Safe to re-run. Note: each run generates a new database password (stored only in Key Vault).

set -euo pipefail

: "${GITHUB_REPO:?Set GITHUB_REPO=owner/repo, e.g. GITHUB_REPO=shubhangi-9595/liquid-biopsy-mlops}"
LOCATION="${LOCATION:-westeurope}"
POSTGRES_LOCATION="${POSTGRES_LOCATION:-$LOCATION}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-liquid-biopsy}"
APP_REG_NAME="gh-liquid-biopsy-deploy"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

step() { printf '\n==> %s\n' "$*"; }
warn() { printf '\nWARNING: %s\n' "$*"; }

step "Azure subscription"
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
az account show --query "{subscription:name, id:id}" -o table

step "Checking allowed regions"
ALLOWED=$(az policy assignment list --disable-scope-strict-match -o json 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit()
locations = set()
for assignment in data:
    params = assignment.get("parameters") or {}
    for key in ("listOfAllowedLocations", "allowedLocations"):
        value = (params.get(key) or {}).get("value")
        if isinstance(value, list):
            locations.update(v.lower() for v in value)
print(" ".join(sorted(locations)))
' || true)
if [ -n "$ALLOWED" ]; then
  echo "Your subscription may deploy to: $ALLOWED"
  for loc in "$LOCATION" "$POSTGRES_LOCATION"; do
    if ! grep -qw "$loc" <<<"$ALLOWED"; then
      echo "ERROR: region '$loc' is not allowed. Rerun with e.g. LOCATION=<one of the regions above>"
      exit 1
    fi
  done
else
  echo "No region restriction found (or not readable). Using $LOCATION."
fi

step "Registering resource providers (first time can take a few minutes)"
for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.OperationalInsights Microsoft.Insights \
  Microsoft.KeyVault Microsoft.DBforPostgreSQL Microsoft.MachineLearningServices Microsoft.Storage \
  Microsoft.ManagedIdentity Microsoft.ContainerService; do
  az provider register --namespace "$ns" --wait -o none
done
az extension add --name containerapp --upgrade --only-show-errors -o none
az extension add --name ml --upgrade --only-show-errors -o none

step "Resource group $RESOURCE_GROUP ($LOCATION)"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" -o none

step "GitHub Actions identity (Entra ID app + OIDC federated credential)"
DEPLOY_PRINCIPAL_ID=""
CLIENT_ID=$(az ad app list --display-name "$APP_REG_NAME" --query "[0].appId" -o tsv 2>/dev/null || true)
if [ -z "$CLIENT_ID" ]; then
  CLIENT_ID=$(az ad app create --display-name "$APP_REG_NAME" --query appId -o tsv 2>/dev/null || true)
fi
if [ -n "$CLIENT_ID" ]; then
  az ad sp show --id "$CLIENT_ID" -o none 2>/dev/null || az ad sp create --id "$CLIENT_ID" -o none
  DEPLOY_PRINCIPAL_ID=$(az ad sp show --id "$CLIENT_ID" --query id -o tsv)
  if ! az ad app federated-credential list --id "$CLIENT_ID" --query "[].name" -o tsv | grep -qx github-production; then
    az ad app federated-credential create --id "$CLIENT_ID" -o none --parameters "{
      \"name\": \"github-production\",
      \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"repo:${GITHUB_REPO}:environment:production\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }"
  fi
  echo "GitHub Actions will log in as app $CLIENT_ID (only from the 'production' environment of $GITHUB_REPO)"
else
  warn "Could not create an Entra ID app registration (your university may block this).
Everything else will still be created. Deploy with: bash infra/deploy_manual.sh"
fi

step "Deploying infrastructure (Bicep): about 10 minutes"
DB_PASSWORD="$(openssl rand -base64 36 | tr -dc 'A-Za-z0-9' | cut -c1-24)Aa1-"
PARAMS_FILE=$(mktemp)
chmod 600 "$PARAMS_FILE"
trap 'rm -f "$PARAMS_FILE"' EXIT
cat >"$PARAMS_FILE" <<EOF
{
  "location": {"value": "$LOCATION"},
  "postgresLocation": {"value": "$POSTGRES_LOCATION"},
  "postgresAdminPassword": {"value": "$DB_PASSWORD"},
  "deployPrincipalId": {"value": "$DEPLOY_PRINCIPAL_ID"}
}
EOF
OUTPUTS=$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --name main \
  --template-file "$REPO_ROOT/infra/bicep/main.bicep" \
  --parameters "@$PARAMS_FILE" \
  --query properties.outputs -o json)
unset DB_PASSWORD

output() { python3 -c "import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]]['value'])" "$OUTPUTS" "$1"; }
ACR_SERVER=$(output acrLoginServer)
ML_WORKSPACE=$(output mlWorkspaceName)
KEY_VAULT=$(output keyVaultName)

step "Configuring GitHub repository $GITHUB_REPO"
CONFIGURED_GITHUB=false
if [ -n "$CLIENT_ID" ] && command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  gh api -X PUT "repos/$GITHUB_REPO/environments/production" --silent
  gh secret set AZURE_CLIENT_ID --repo "$GITHUB_REPO" --body "$CLIENT_ID"
  gh secret set AZURE_TENANT_ID --repo "$GITHUB_REPO" --body "$TENANT_ID"
  gh secret set AZURE_SUBSCRIPTION_ID --repo "$GITHUB_REPO" --body "$SUBSCRIPTION_ID"
  gh variable set AZURE_RESOURCE_GROUP --repo "$GITHUB_REPO" --body "$RESOURCE_GROUP"
  CONFIGURED_GITHUB=true
  echo "Secrets, variable and 'production' environment set with the GitHub CLI."
fi

cat <<EOF

============================================================================
 Azure is ready
============================================================================
 Resource group      $RESOURCE_GROUP
 Container registry  $ACR_SERVER
 Key Vault           $KEY_VAULT
 Azure ML workspace  $ML_WORKSPACE  (https://ml.azure.com)
EOF

if [ -n "$CLIENT_ID" ] && [ "$CONFIGURED_GITHUB" = false ]; then
  cat <<EOF

 Add these in GitHub: repo -> Settings -> Secrets and variables -> Actions

   Secrets tab
     AZURE_CLIENT_ID        $CLIENT_ID
     AZURE_TENANT_ID        $TENANT_ID
     AZURE_SUBSCRIPTION_ID  $SUBSCRIPTION_ID

   Variables tab
     AZURE_RESOURCE_GROUP   $RESOURCE_GROUP

 And create an environment named exactly: production
   repo -> Settings -> Environments -> New environment
EOF
fi

cat <<EOF

 Next: GitHub -> Actions -> CD -> Run workflow (or push to main).
 The first deployment creates the API and prints its URL in the run summary.

 Optional Kubernetes + GitOps path:  bash infra/aks.sh up
 Pause the database to save credit:  bash infra/pause.sh
============================================================================
EOF
