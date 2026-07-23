#!/usr/bin/env bash
set -euo pipefail

# End-to-end incident demo:
#   inject latency + errors on EVERY orders-api replica  ->  burn-rate alert fires
#   ->  run the AI SRE agent  ->  save its RCA to docs/rca-report-example.md  ->  reset.
#
# Chaos state is in-memory per pod and the Service load-balances, so we port-forward
# to each replica directly and set the same chaos on all of them (never scaled down).
#
# Usage:
#   scripts/demo-failure.sh                 # full demo (500ms latency + 100% errors)
#   scripts/demo-failure.sh chaos 300 0.4   # inject only, on all replicas
#   scripts/demo-failure.sh reset           # clear chaos on all replicas

NS="apps"
AGENT_NS="sre-agent"
SELECTOR="app.kubernetes.io/name=orders-api"
ALERT="OrdersApiErrorBudgetFastBurn"
LOCAL_PORT_BASE=18080
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

MODE="demo"
case "${1:-}" in
  reset) MODE="reset" ;;
  chaos) MODE="chaos"; LATENCY_MS="${2:-300}"; ERROR_RATE="${3:-0.4}" ;;
  *)     LATENCY_MS="${1:-500}"; ERROR_RATE="${2:-1.0}" ;;
esac

# Run one chaos call against a single pod via a short-lived port-forward.
on_pod() {
  local pod="$1" lport="$2" action="$3"
  kubectl -n "$NS" port-forward "pod/${pod}" "${lport}:8000" >/dev/null 2>&1 &
  local pf=$!
  local ok=""
  for _ in $(seq 1 40); do
    curl -sf -o /dev/null "http://127.0.0.1:${lport}/healthz" 2>/dev/null && { ok=1; break; }
    sleep 0.25
  done
  if [ -n "$ok" ]; then
    if [ "$action" = "reset" ]; then
      curl -sS -X POST "http://127.0.0.1:${lport}/chaos/reset" && echo "  <- ${pod}"
    else
      curl -sS -X POST "http://127.0.0.1:${lport}/chaos/latency?ms=${LATENCY_MS}" >/dev/null
      curl -sS -X POST "http://127.0.0.1:${lport}/chaos/errors?rate=${ERROR_RATE}" >/dev/null
      echo "  <- ${pod}: latency=${LATENCY_MS}ms errors=${ERROR_RATE}"
    fi
  else
    echo "  ! ${pod}: port-forward not ready" >&2
  fi
  kill "$pf" 2>/dev/null || true
  wait "$pf" 2>/dev/null || true
}

apply_to_all() {
  local action="$1"
  local pods port
  pods=$(kubectl -n "$NS" get pods -l "$SELECTOR" -o jsonpath='{.items[*].metadata.name}')
  [ -n "$pods" ] || { echo "no orders-api pods found" >&2; exit 1; }
  port="$LOCAL_PORT_BASE"
  for pod in $pods; do on_pod "$pod" "$port" "$action"; port=$((port + 1)); done
}

alert_firing() {
  local prom
  prom=$(kubectl -n monitoring get pod -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}')
  local q="ALERTS%7Balertname%3D%22${ALERT}%22%2Calertstate%3D%22firing%22%7D"
  kubectl -n monitoring exec "$prom" -c prometheus -- \
    sh -c "wget -qO- 'http://localhost:9090/api/v1/query?query=${q}'" 2>/dev/null | grep -q '"alertstate":"firing"'
}

if [ "$MODE" = "reset" ]; then
  echo "Clearing chaos on all orders-api replicas..."
  apply_to_all reset
  echo "Done."
  exit 0
fi

echo "==> Injecting chaos on all orders-api replicas (latency=${LATENCY_MS}ms errors=${ERROR_RATE})"
apply_to_all inject

if [ "$MODE" = "chaos" ]; then
  echo "Chaos injected. Run 'scripts/demo-failure.sh reset' to clear."
  exit 0
fi

echo "==> Waiting for ${ALERT} to fire (multiwindow burn needs a few minutes of sustained errors)..."
deadline=$(( $(date +%s) + 900 ))
until alert_firing; do
  [ "$(date +%s)" -lt "$deadline" ] || { echo "!! alert did not fire within 15m; leaving chaos on for inspection" >&2; exit 1; }
  sleep 15
  echo "    ...still waiting ($(( (deadline - $(date +%s)) )) s budget left)"
done
echo "==> ${ALERT} is FIRING."

JOB="sre-agent-demo-$(date +%s)"
echo "==> Running the AI SRE agent as Job/${JOB}"
kubectl -n "$AGENT_NS" create job --from=cronjob/sre-agent "$JOB"
kubectl -n "$AGENT_NS" wait --for=condition=complete "job/${JOB}" --timeout=240s

OUT="${ROOT}/docs/rca-report-example.md"
echo "==> Saving RCA to ${OUT}"
kubectl -n "$AGENT_NS" logs "job/${JOB}" \
  | sed -n '/===RCA-START===/,/===RCA-END===/p' | sed '1d;$d' > "$OUT"

echo "==> Resetting chaos"
apply_to_all reset

echo "==> Done. RCA written to ${OUT} ($(wc -l < "$OUT") lines)."
