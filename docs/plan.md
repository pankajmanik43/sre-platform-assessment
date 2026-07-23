# Build Plan

## Current state
- k3d cluster, ArgoCD v3.4.5, root app (app-of-apps) at argocd/platform/
- kube-prometheus-stack, loki, tempo: Synced/Healthy in monitoring namespace
- Grafana datasources: Loki + Tempo added via kps values; Loki derived field trace_id -> Tempo,
  Tempo tracesToLogs -> Loki (service.name -> service_name), service map + node graph. Verified via API.
- Grafana Alloy DaemonSet (chart 1.11.0) ships pod logs to Loki (not in original plan — needed for
  "logs in Grafana"; reads /var/log/pods, strips CRI, labels service_name from app.kubernetes.io/name)
- Wave-0 namespaces apps/temporal/sre-agent: PSA labels + default-deny + allow-dns baseline NetworkPolicies
- orders-api (apps ns) live: 2 replicas Healthy, RED metrics scraped (incl. 500s), OTLP traces in Tempo
  (with db.query.orders child span), JSON logs in Loki with trace_id. load-generator at 1 req/s.
  Image is locally built + k3d-imported via scripts/build-orders-api.sh.
- Alloy hardened: uid 0 (pod logs are 0640 root:root) but all caps dropped, read-only rootfs + /var/log mount.
- SLO: PrometheusRule orders-api-slo — 5m/1h error-ratio + 30d availability/error-budget recording rules;
  multiwindow burn-rate alert OrdersApiErrorBudgetFastBurn (14.4x on 1h AND 5m). Loaded + health=ok.
- Grafana dashboard "orders-api" (ConfigMap, sidecar-loaded): RED, availability, error budget gauge, logs+trace links.
- scripts/demo-failure.sh: full incident demo — deterministic chaos on all replicas -> burn alert ->
  run agent -> save RCA -> reset. Verified end-to-end (real RCA in docs/rca-report-example.md).
- Sealed Secrets: vendored controller v0.38.4; agent Anthropic key delivered as a SealedSecret.
- AI SRE agent (chunk 7) DONE: suspended CronJob in sre-agent ns, read-only RBAC (get/list pods+events
  in apps), NetworkPolicy (monitoring + apiserver :6443 + Anthropic :443). All 5 collectors verified;
  one claude-opus-4-8 call (adaptive thinking) -> full RCA. Demo produced a real, well-grounded report.

## Remaining build order
6. Temporal via Helm (bundled PostgreSQL dev config — document why), one health-check workflow  [skipped for now]
8. README: quickstart, mermaid architecture diagram, design decisions table, agent section, roadmap

## Design decisions to document
- k3d over VM k3s (reviewers need only Docker)
- Plain Prometheus, no Mimir (single-node; would use Mimir/Thanos multi-cluster)
- No OTel Collector locally (prod: DaemonSet with tail sampling)
- Grafana Alloy for log shipping (promtail is EOL); DaemonSet reading /var/log/pods off the node
- No CPU limits (throttling; requests handle scheduling)
- Temporal on bundled PostgreSQL
- Sealed Secrets for agent API key; grafana admin password left simple for local (noted)
- ArgoCD v3.4.5 pin (k8s 1.35 terminatingReplicas schema; CRDs need server-side apply)
