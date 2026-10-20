#!/usr/bin/env bash
# Save Azure credit while you're not using the project.
#
#   bash infra/pause.sh           stop PostgreSQL and AKS (if created)
#   bash infra/pause.sh resume    start them again
#
# The API on Container Apps already scales to zero by itself. While the database is
# stopped, the API keeps answering predictions but does not store them
# (/health shows database=error). Azure restarts a stopped PostgreSQL server
# automatically after 7 days, so pause again if you need to.

set -euo pipefail
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-liquid-biopsy}"

PG=$(az postgres flexible-server list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)
AKS=$(az aks list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)

if [ "${1:-pause}" = "resume" ]; then
  [ -n "$PG" ] && az postgres flexible-server start -g "$RESOURCE_GROUP" -n "$PG" -o none && echo "PostgreSQL started"
  [ -n "$AKS" ] && az aks start -g "$RESOURCE_GROUP" -n "$AKS" -o none && echo "AKS started"
else
  [ -n "$PG" ] && az postgres flexible-server stop -g "$RESOURCE_GROUP" -n "$PG" -o none && echo "PostgreSQL stopped"
  [ -n "$AKS" ] && az aks stop -g "$RESOURCE_GROUP" -n "$AKS" -o none && echo "AKS stopped"
fi
true
