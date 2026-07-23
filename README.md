# SRE Platform Assessment

Production-grade observability platform on k3d, deployed entirely via ArgoCD.
See `docs/plan.md` for the build plan and current state.

## Design decisions

| Decision | Why |
| --- | --- |
| **k3d** over a VM-based k3s | Reviewers need only Docker; the cluster is reproducible from `bootstrap/`. |
| Plain Prometheus, no Mimir/Thanos | Single node; multi-cluster long-term storage would be Mimir/Thanos in prod. |
| **No OTel Collector locally** | `orders-api` exports OTLP/gRPC straight to Tempo. In prod a Collector DaemonSet would do batching + tail sampling. |
| **No CPU limits** on workloads | CPU requests drive scheduling; a limit only adds throttling and tail latency. Memory is limited (OOM is the real failure mode). |
| **Grafana Alloy** for log shipping, not Promtail | Promtail is deprecated/EOL (Grafana's own successor is Alloy). Alloy tails `/var/log/pods` and pushes to Loki. |
| Alloy runs as **root, but with zero Linux capabilities** | Pod log files are `0640 root:root`, so uid 0 is required to read them — but it reads them via file *ownership*, so `capabilities: drop [ALL]`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`, and a **read-only** `/var/log` host mount are all kept. Non-root would need `CAP_DAC_READ_SEARCH`, which is a broader grant (bypasses *all* file DAC) than reading files it already owns. |

## Layout
- `bootstrap/` — one-shot k3d + ArgoCD install (the only thing applied outside ArgoCD).
- `argocd/platform/` — app-of-apps children (Applications).
- `manifests/` — raw manifests referenced by those Applications.
- `services/` — application source (e.g. `orders-api`).
- `scripts/` — build + demo helpers.
