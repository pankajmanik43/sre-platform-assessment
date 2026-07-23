#!/usr/bin/env bash
set -euo pipefail

# Builds the sre-agent image and imports it into k3d (no registry in this setup).
CLUSTER_NAME="sre-assessment"
IMAGE="sre-agent:0.1.0"
DIR="$(dirname "$0")/../services/sre-agent"

docker build -t "${IMAGE}" "${DIR}"
k3d image import "${IMAGE}" -c "${CLUSTER_NAME}"

echo "Imported ${IMAGE} into k3d cluster ${CLUSTER_NAME}"
