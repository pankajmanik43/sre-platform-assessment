#!/usr/bin/env bash
set -euo pipefail

# Inject chaos into EVERY orders-api replica deterministically.
# Chaos state is in-memory per pod, and the Service load-balances across pods, so
# hitting the Service would only reach one replica per call. Instead we port-forward
# to each pod directly and set the same chaos on all of them. Replicas are never scaled.
#
# Usage:
#   scripts/demo-failure.sh                 # default: 800ms latency + 30% errors on all pods
#   scripts/demo-failure.sh 500 0.5         # 500ms latency + 50% errors
#   scripts/demo-failure.sh reset           # clear chaos on all pods

NS="apps"
SELECTOR="app.kubernetes.io/name=orders-api"
LOCAL_PORT_BASE=18080

if [[ "${1:-}" == "reset" ]]; then
  MODE="reset"
else
  MODE="inject"
  LATENCY_MS="${1:-800}"
  ERROR_RATE="${2:-0.3}"
fi

# Send an HTTP call to a single pod via a short-lived port-forward.
on_pod() {
  local pod="$1" lport="$2"
  kubectl -n "$NS" port-forward "pod/${pod}" "${lport}:8000" >/dev/null 2>&1 &
  local pf=$!
  trap 'kill "$pf" 2>/dev/null || true' RETURN

  # wait for the tunnel to accept connections
  local ok=""
  for _ in $(seq 1 40); do
    if curl -sf -o /dev/null "http://127.0.0.1:${lport}/healthz" 2>/dev/null; then ok=1; break; fi
    sleep 0.25
  done
  if [[ -z "$ok" ]]; then
    echo "  ! ${pod}: port-forward did not become ready" >&2
    return 1
  fi

  if [[ "$MODE" == "reset" ]]; then
    curl -sS -X POST "http://127.0.0.1:${lport}/chaos/reset" && echo "  <- ${pod}"
  else
    curl -sS -X POST "http://127.0.0.1:${lport}/chaos/latency?ms=${LATENCY_MS}" && echo "  <- ${pod} latency"
    curl -sS -X POST "http://127.0.0.1:${lport}/chaos/errors?rate=${ERROR_RATE}" && echo "  <- ${pod} errors"
  fi
}

pods=$(kubectl -n "$NS" get pods -l "$SELECTOR" -o jsonpath='{.items[*].metadata.name}')
if [[ -z "$pods" ]]; then
  echo "no orders-api pods found in namespace ${NS}" >&2
  exit 1
fi

if [[ "$MODE" == "reset" ]]; then
  echo "Clearing chaos on all orders-api replicas..."
else
  echo "Injecting chaos on all orders-api replicas: latency=${LATENCY_MS}ms errors=${ERROR_RATE}"
fi

port="$LOCAL_PORT_BASE"
for pod in $pods; do
  on_pod "$pod" "$port"
  port=$((port + 1))
done

echo "Done ($(echo "$pods" | wc -w) replica(s))."
