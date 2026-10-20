#!/usr/bin/env bash
# Runs once when the Codespace / dev container is created.
set -euo pipefail

KIND_VERSION=v0.33.0

pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .

# kind = Kubernetes in Docker, for testing the manifests locally (make kind-up)
curl -sSLo /tmp/kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64"
sudo install -m 0755 /tmp/kind /usr/local/bin/kind

echo
echo "Ready. Try: make help"
