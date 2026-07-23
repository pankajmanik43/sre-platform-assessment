# Build Plan

## Current state
- k3d cluster, ArgoCD v3.4.5, root app (app-of-apps) at argocd/platform/
- kube-prometheus-stack, loki, tempo: Synced/Healthy in monitoring namespace

## Remaining build order
1. Grafana datasources for Loki + Tempo via kps values; derived fields linking trace_id in logs to Tempo traces
2. Wave-0: namespaces (apps, temporal, sre-agent) with labels + baseline NetworkPolicies
3. Sample API "orders-api" (apps namespace): FastAPI + OTel
   - GET /healthz, /readyz, /api/orders (fake db call span, 5-20ms), POST /api/orders (0.5% baked-in 500s)
   - /chaos/latency?ms=, /chaos/errors?rate=, /chaos/reset
   - Metrics: prometheus_client — http_requests_total{path,method,status}, http_request_duration_seconds histogram
   - Traces: OTel auto-instrumentation, OTLP/gRPC direct to Tempo (no collector — note in README)
   - Logs: structlog JSON to stdout with trace_id/span_id injected
   - Manifests: 2 replicas, requests/limits (no CPU limit — document why), probes, securityContext, PDB, NetworkPolicy
   - load-generator Deployment hitting /api/orders every 1s
4. SLO + alert: recording rules for availability SLI (1 - 5xx/total on /api/orders), 99.5% target, multiwindow burn-rate alert (5m + 1h)
5. One hand-built Grafana dashboard as ConfigMap: RED metrics, error budget, logs panel, trace links
6. Temporal via Helm (bundled PostgreSQL dev config — document why), one health-check workflow
7. AI SRE agent (sre-agent namespace): Python CLI as K8s Job
   - Deterministic collectors: Alertmanager firing alerts, Prometheus (error rate, latency, restarts, burn rate), Loki error logs (cap 200 lines), Tempo slow/errored traces, K8s events
   - One-two Claude API calls → structured RCA markdown (Summary/Timeline/Root Cause+confidence/Evidence/Blast Radius/Remediation/Unknown)
   - Read-only RBAC, API key via Sealed Secret, token budget via evidence truncation
   - scripts/demo-failure.sh: chaos latency injection → burn alert → run agent → commit RCA to docs/rca-report-example.md
8. README: quickstart, mermaid architecture diagram, design decisions table, agent section, roadmap

## Design decisions to document
- k3d over VM k3s (reviewers need only Docker)
- Plain Prometheus, no Mimir (single-node; would use Mimir/Thanos multi-cluster)
- No OTel Collector locally (prod: DaemonSet with tail sampling)
- No CPU limits (throttling; requests handle scheduling)
- Temporal on bundled PostgreSQL
- Sealed Secrets for agent API key; grafana admin password left simple for local (noted)
- ArgoCD v3.4.5 pin (k8s 1.35 terminatingReplicas schema; CRDs need server-side apply)
