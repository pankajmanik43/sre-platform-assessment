# sre-agent

An AI SRE agent that turns live platform signals into a structured root-cause
analysis. Packaged as a Kubernetes Job (a suspended CronJob triggered on demand)
in the `sre-agent` namespace.

## How it works
1. **Deterministic collectors (no LLM in the loop):**
   - **Alertmanager** — firing alerts as the trigger context.
   - **Prometheus** — error ratio, latency p50/p95/p99, burn rate (5m + 1h), pod restarts, error budget.
   - **Loki** — `orders-api` logs with `status >= 500`, capped at 200 lines (each carries a `trace_id`).
   - **Tempo** — slow (`duration > 300ms`) and errored (`status = error`) trace search, with a span breakdown of a sample.
   - **Kubernetes API** — pod restart counts + last termination state, and Warning events.
2. **Evidence bundle → one Claude API call.** All signals are truncated to a fixed
   token budget, serialized, and sent to `claude-opus-4-8` (adaptive thinking) which
   writes the RCA. Each collector is isolated — a failure is recorded as evidence,
   not a crash.
3. **Output** — the RCA markdown is printed to stdout between `===RCA-START===` /
   `===RCA-END===`; all diagnostics go to stderr, so `scripts/demo-failure.sh` can
   extract just the report.

## RCA structure
Summary · Timeline · Root Cause (with confidence) · Evidence (citing specific
metrics, log lines, trace IDs) · Blast Radius · Remediation (immediate + preventive)
· What the agent could not determine.

## Security
- **Read-only RBAC** — a ClusterRole with `get`/`list` on `pods` and `events` only,
  bound (via RoleBinding) to the `apps` namespace. No write access anywhere.
- **API key via Sealed Secret** — the sealed-secrets controller is deployed, and
  the key is delivered as an encrypted `SealedSecret` (never plaintext in git).
  Because a `SealedSecret` is encrypted against **one specific cluster's** controller
  keypair, it can't be a portable synced resource — so the Job reads a Secret named
  `sre-agent-api-key` (key `ANTHROPIC_API_KEY`) that you provide per cluster:
  > ```
  > # Quick path — create the Secret directly:
  > kubectl create secret generic sre-agent-api-key -n sre-agent \
  >   --from-literal=ANTHROPIC_API_KEY=<your-key>
  > ```
  > Or use the real sealed-secrets flow: seal your key against your cluster's
  > controller with `kubeseal` and apply it (example shape:
  > `docs/examples/sealed-api-key.example.yaml`). The controller then unseals it
  > into the same `sre-agent-api-key` Secret.
- **NetworkPolicy** — egress limited to the monitoring backends and HTTPS (Kubernetes
  API + `api.anthropic.com`); default-deny otherwise.
- Non-root, read-only root filesystem, all capabilities dropped.

## Token budget
Bounded by evidence truncation: logs ≤ 200 lines (≤ 400 chars each), traces ≤ 8,
Warning events ≤ 40. Keeps the single request well within context.
