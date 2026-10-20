#!/usr/bin/env bash
# Optional: Azure Kubernetes Service + Argo CD (GitOps) deployment path.
#
#   bash infra/aks.sh up       create the cluster, install Argo CD, connect it to your repo
#   bash infra/aks.sh argocd   print how to open the Argo CD dashboard
#   bash infra/aks.sh stop     stop the cluster (no node charges while stopped)
#   bash infra/aks.sh start    start it again (Argo CD then syncs to the latest commit)
#   bash infra/aks.sh down     delete the cluster (keeps everything else)
#   bash infra/aks.sh status
#
# Requires infra/setup.sh to have run. GITHUB_REPO defaults to this clone's origin remote.

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-liquid-biopsy}"
ARGOCD_VERSION="v3.5.5"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY="$REPO_ROOT/k8s/overlays/aks"

aks_name() { az aks list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv; }

github_repo() {
  if [ -n "${GITHUB_REPO:-}" ]; then echo "$GITHUB_REPO"; return; fi
  git -C "$REPO_ROOT" remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##'
}

cmd_up() {
  local repo
  repo=$(github_repo)
  echo "==> Creating AKS cluster (about 10 minutes)"
  OUTPUTS=$(az deployment group create -g "$RESOURCE_GROUP" -n aks \
    --template-file "$REPO_ROOT/infra/bicep/aks.bicep" --query properties.outputs -o json)
  out() { python3 -c "import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]]['value'])" "$OUTPUTS" "$1"; }
  local aks kv tenant client acr
  aks=$(out aksName); kv=$(out keyVaultName); tenant=$(out tenantId)
  client=$(out workloadClientId); acr=$(out acrLoginServer)

  az aks get-credentials -g "$RESOURCE_GROUP" -n "$aks" --overwrite-existing

  echo "==> Writing your Azure IDs into k8s/overlays/aks (these are IDs, not secrets)"
  cat >"$OVERLAY/azure.env" <<EOF
KEYVAULT_NAME=$kv
TENANT_ID=$tenant
WORKLOAD_CLIENT_ID=$client
EOF
  sed -i -E "s#newName: .*/liquid-biopsy-api#newName: $acr/liquid-biopsy-api#" "$OVERLAY/kustomization.yaml"

  echo "==> Installing Argo CD $ARGOCD_VERSION"
  kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -n argocd --server-side --force-conflicts \
    -f "https://raw.githubusercontent.com/argoproj/argo-cd/$ARGOCD_VERSION/manifests/install.yaml" >/dev/null
  kubectl -n argocd rollout status deploy/argocd-server --timeout=300s

  echo "==> Connecting Argo CD to https://github.com/$repo (k8s/overlays/aks on main)"
  sed "s#REPLACE_OWNER/REPLACE_REPO#$repo#" "$REPO_ROOT/k8s/argocd/application.yaml" | kubectl apply -f -

  cat <<EOF

============================================================================
 AKS + Argo CD are running. One step left, the GitOps way:

   git add k8s/overlays/aks && git commit -m "Configure AKS overlay" && git push

 Argo CD deploys whatever is in Git, so it needs these values committed.
 After that, every release from the CD pipeline updates the image tag in
 k8s/overlays/aks/kustomization.yaml and Argo CD rolls it out automatically.

 Watch it:   kubectl -n liquid-biopsy get pods -w
 Call it:    kubectl -n liquid-biopsy port-forward svc/liquid-biopsy-api 8080:80
             then open http://localhost:8080/docs
 Dashboard:  bash infra/aks.sh argocd

 Remember:   bash infra/aks.sh stop   when you're done (nodes cost money while running)
============================================================================
EOF
}

cmd_argocd() {
  local pw
  pw=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d)
  echo "Run:  kubectl -n argocd port-forward svc/argocd-server 8443:443"
  echo "Open: https://localhost:8443  (accept the self-signed certificate)"
  echo "User: admin   Password: $pw"
}

cmd_stop() { az aks stop -g "$RESOURCE_GROUP" -n "$(aks_name)"; echo "Stopped. No node charges until 'start'."; }
cmd_start() {
  az aks start -g "$RESOURCE_GROUP" -n "$(aks_name)"
  az aks get-credentials -g "$RESOURCE_GROUP" -n "$(aks_name)" --overwrite-existing
}
cmd_down() {
  local aks
  aks=$(aks_name)
  [ -n "$aks" ] && az aks delete -g "$RESOURCE_GROUP" -n "$aks" --yes
  az identity delete -g "$RESOURCE_GROUP" -n id-lb-aks-workload 2>/dev/null || true
  echo "AKS deleted. Container Apps deployment, database and registry are untouched."
}
cmd_status() { az aks list -g "$RESOURCE_GROUP" --query "[].{name:name, power:powerState.code, k8s:kubernetesVersion}" -o table; }

case "${1:-}" in
  up) cmd_up ;;
  argocd) cmd_argocd ;;
  stop) cmd_stop ;;
  start) cmd_start ;;
  down) cmd_down ;;
  status) cmd_status ;;
  *) echo "Usage: bash infra/aks.sh {up|argocd|stop|start|down|status}"; exit 1 ;;
esac
