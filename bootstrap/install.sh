#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="sre-assessment"
ARGOCD_VERSION="v3.4.5"

# k3d = k3s in docker; only prerequisite for reviewers is docker itself
if ! command -v k3d &>/dev/null; then
  curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
fi

if ! k3d cluster list | grep -q "^${CLUSTER_NAME}"; then
  k3d cluster create "${CLUSTER_NAME}" \
    --servers 1 --agents 0 \
    --k3s-arg "--disable=traefik@server:0" \
    --port "3000:80@loadbalancer"
fi
kubectl wait --for=condition=Ready node --all --timeout=120s

kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd --server-side -f \
  "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
kubectl -n argocd wait --for=condition=Available deploy/argocd-server --timeout=300s

kubectl apply -f "$(dirname "$0")/root-app.yaml"

echo "Bootstrap complete. Watch: kubectl -n argocd get applications -w"
