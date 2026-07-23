#!/usr/bin/env bash
set -euo pipefail

# Builds all locally-authored images and imports them into k3d (no registry here).
# ArgoCD deploys the manifests that reference them.
DIR="$(dirname "$0")"
CLUSTER="sre-assessment"
NODE="k3d-${CLUSTER}-server-0"

bash "${DIR}/build-orders-api.sh"
bash "${DIR}/build-sre-agent.sh"
bash "${DIR}/build-temporal-healthcheck.sh"

# `k3d image import` can occasionally report success without the image actually
# landing in the node (seen under heavy load). Verify each is present in the
# node's containerd and re-import if not, so pods never get stuck ImagePullBackOff.
for img in orders-api:0.1.0 sre-agent:0.1.0 temporal-healthcheck:0.1.0; do
  name="${img%%:*}"; tag="${img##*:}"
  for attempt in 1 2 3; do
    if docker exec "$NODE" crictl images 2>/dev/null | grep -qE "library/${name}[[:space:]]+${tag}[[:space:]]"; then
      break
    fi
    echo "verify: ${img} not present in cluster — re-importing (attempt ${attempt})"
    k3d image import "$img" -c "$CLUSTER"
  done
done
echo "All images built, imported, and verified."
