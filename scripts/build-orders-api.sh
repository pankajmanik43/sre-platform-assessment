#!/usr/bin/env bash
set -euo pipefail

# orders-api ships as a locally-built image imported into k3d (no registry in this setup).
# ArgoCD deploys the manifests; this script only produces the image they reference.
CLUSTER_NAME="sre-assessment"
IMAGE="orders-api:0.1.0"
DIR="$(dirname "$0")/../services/orders-api"

docker build -t "${IMAGE}" "${DIR}"
k3d image import "${IMAGE}" -c "${CLUSTER_NAME}"

echo "Imported ${IMAGE} into k3d cluster ${CLUSTER_NAME}"
