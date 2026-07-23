#!/usr/bin/env bash
set -euo pipefail

# Builds all locally-authored images and imports them into k3d (no registry here).
# ArgoCD deploys the manifests that reference them.
DIR="$(dirname "$0")"
bash "${DIR}/build-orders-api.sh"
bash "${DIR}/build-sre-agent.sh"
bash "${DIR}/build-temporal-healthcheck.sh"
echo "All images built and imported."
