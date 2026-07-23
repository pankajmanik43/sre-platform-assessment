# Project: Staff SRE Assessment
Production-grade local platform on k3d (k3s in Docker). Full plan in docs/plan.md.

## Rules
- Everything deploys via ArgoCD from this repo — never kubectl apply manifests directly (bootstrap/ is the only exception)
- Conventional commits, one logical unit each; commit fixes as fix:, don't squash
- All workloads: resource requests/limits, probes, securityContext (runAsNonRoot, readOnlyRootFilesystem), NetworkPolicies
- Minimal comments — only where a human would leave a note
- Pin all chart and image versions
- Verify each component works (signals landing in Grafana) before moving to the next
- Stop for review at the end of each chunk before starting the next
