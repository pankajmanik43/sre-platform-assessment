#!/usr/bin/env bash
set -euo pipefail

# Builds the temporal-healthcheck worker image and imports it into k3d.
CLUSTER_NAME="sre-assessment"
IMAGE="temporal-healthcheck:0.1.0"
DIR="$(dirname "$0")/../services/temporal-healthcheck"

docker build -t "${IMAGE}" "${DIR}"
k3d image import "${IMAGE}" -c "${CLUSTER_NAME}"

echo "Imported ${IMAGE} into k3d cluster ${CLUSTER_NAME}"
