# RCA: orders-api

## Summary
`orders-api` is returning HTTP 500 on effectively 100% of `/api/orders` traffic (`error_ratio_5m: 1.0`), triggering the critical `OrdersApiErrorBudgetFastBurn` page at a ~200x burn rate. The 30-day SLO is already blown (`availability_ratio_30d: 0.9653` vs. 0.995 target; `error_budget_remaining_30d: -5.93973`). The most probable trigger is a deliberate error-injection ("chaos") fault, evidenced by a `POST /chaos/errors` trace and the near-uniform ~500 ms failure signature.

## Timeline
- **2026-07-23T06:02:02Z** — `Watchdog` active (expected; alerting pipeline healthy).
- **2026-07-23T06:16:40–06:16:41Z** — `KubeProxyDown`, `KubeControllerManagerDown`, `KubeSchedulerDown` fire (control-plane scrape targets missing).
- **2026-07-23T09:39:09Z** — `KubeAPIErrorBudgetBurn` (warning) fires.
- **2026-07-23T11:24:07–11:24:13Z** — Earliest readiness/liveness probe timeouts on both pods (`gltm4`, `h6p6x`).
- **2026-07-23T11:36:38Z** — Earliest 500 in the provided log window (`trace_id 52a2c517f11b7ac5d176bf3a6704bda9`).
- **2026-07-23T11:37:07Z** — Last readiness-probe failure recorded on `h6p6x` (count 19).
- **2026-07-23T11:40:05Z** — Last liveness-probe failure on `gltm4` (count 11).
- **2026-07-23T11:41:16.327Z** — `OrdersApiErrorBudgetFastBurn` (critical) fires (>14.4x burn).
- **2026-07-23T11:41:50Z** — Latest 500 in the log window (`trace_id 3ffff07aef925bf171daae3e11aa92e7`).

## Root Cause
Most likely a **deliberate fault/error injection into `orders-api`** (chaos experiment) producing constant HTTP 500s. **Confidence: Medium.**

Why:
- A trace named **`POST /chaos/errors`** (`traceID fd1de625301b1a957702d2540f7ffe03`, 1339 ms) appears in `slow_traces` — an explicit error-injection endpoint exposed by the service.
- The failure signature is highly uniform: nearly every 500 log entry has `duration_ms` clustered tightly around **~500 ms** (e.g., 500.55–602.79 ms), consistent with a fixed injected delay-then-fail rather than variable downstream latency.
- Span breakdown for errored traces (`686323b5…`, `cfc2532d…`) shows the root `GET /api/orders` span at `STATUS_CODE_ERROR` (~501–521 ms) with only trivial `http send` child spans (0.01–0.1 ms) and **no downstream database/dependency span** — the error originates inside the app, not from a slow/failing dependency.
- `pod_restarts_1h: 0` rules out crash-looping; the app is up and deliberately erroring.

This is Medium (not High) confidence because the evidence does not include the actual injection config, feature flag, or a log line explicitly stating an injected fault; the `/chaos/errors` endpoint and uniform signature are strong but circumstantial.

## Evidence
- **Metrics:** `error_ratio_5m: 1.0` (100% errors, last 5m), `error_ratio_1h: 0.10364`, `burn_rate_5m: 200.0`, `burn_rate_1h: 20.73`, `availability_ratio_30d: 0.9653`, `error_budget_remaining_30d: -5.93973`, `request_rate_5m: 0.63509` rps, `pod_restarts_1h: 0.0`.
- **Alert:** `OrdersApiErrorBudgetFastBurn` — "5xx ratio on /api/orders exceeds 7.2% over both the 1h and 5m windows… budget exhausted in ~2 days," `activeAt 2026-07-23T11:41:16.327Z`.
- **Logs (all `status: 500`, `path /api/orders`, GET and POST):** e.g. `trace_id c75b6f2c61a12536a24cfc94a4e9897f` (POST, 602.79 ms, 11:37:04Z); `trace_id 1a6d29f3568512b7d34c0fef9f0ba42c` (POST, 505.05 ms, 11:41:32Z); `trace_id 3ffff07aef925bf171daae3e11aa92e7` (GET, 502.02 ms, 11:41:50Z). 200 lines returned, all 500s clustered ~500 ms.
- **Traces:** `POST /chaos/errors` (`fd1de625301b1a957702d2540f7ffe03`, 1339 ms) in `slow_traces`; errored traces `686323b567363ae4845d6eadbed54f44` (521 ms), `cfc2532d437a2d0b2166c5ff52fcae1` (501 ms), `46e6384a66e11b3fb1fcf7c0233c1264` (POST, 501 ms), `999c003796de99bf08631fac74c320aa` (POST, 520 ms). Span breakdown: root `/api/orders` spans marked `STATUS_CODE_ERROR`, no dependency spans.
- **Kubernetes:** `Pod/orders-api-84dbfcdd9b-gltm4` Readiness probe failed x17 (`context deadline exceeded`, last 11:24:13Z) and Liveness x11 (last 11:40:05Z); `Pod/orders-api-84dbfcdd9b-h6p6x` Readiness x19 (last 11:37:07Z) and Liveness x10 (last 11:24:07Z). Both `orders-api` pods report `restarts: 0`.

## Blast Radius
- **Service:** `orders-api` in namespace `apps` — both replicas affected (`orders-api-84dbfcdd9b-gltm4` and `-h6p6x` show probe failures).
- **Endpoints:** Both `GET` and `POST /api/orders` returning 500 (confirmed in logs and traces), i.e. order reads and writes are failing.
- **User impact:** Effectively total for the current 5-minute window (`error_ratio_5m: 1.0`); over the trailing hour ~10.4% of requests failed. Traffic volume is low (`~0.64 rps`), so absolute request count is modest, but the SLO is fully breached and error budget is negative.
- The three control-plane alerts (`KubeProxyDown`, `KubeScheduler/ControllerManagerDown`, `KubeAPIErrorBudgetBurn`) are cluster-wide monitoring/control-plane concerns; there is no evidence linking them to the `orders-api` 500s.

## Remediation
**Immediate:**
- Verify and disable any active chaos/error-injection targeting `orders-api` (check the `/chaos/errors` endpoint / associated flag or config). If confirmed, halting the experiment should immediately drop `error_ratio_5m`.
- If injection cannot be confirmed quickly, roll back/redeploy `orders-api` to the last known-good revision and watch `error_ratio_5m` and probe status.
- Confirm recovery via `/readyz` and `/healthz` returning within timeout and `error_ratio_5m` trending to ~0.

**Preventive:**
- Gate chaos experiments behind explicit environment guards so they cannot run against a production SLO-bound service, and emit a distinct, greppable log/metric when fault injection is active.
- Add an alert/annotation that surfaces active chaos experiments on the same dashboard as SLO burn to avoid ambiguous pages.
- Tune liveness/readiness probes and add a synthetic canary on `/api/orders` so injected/real failures are caught before budget burn reaches 200x.
- Investigate the separately-firing control-plane alerts (`KubeProxyDown`, etc., active since 06:16Z) independently.

## What I could not determine
- **Definitive confirmation of injection:** No log line, config, or annotation explicitly states a fault was injected; the `/chaos/errors` trace and uniform ~500 ms signature are strong but circumstantial. The actual error message/exception for the 500s is **not** in the evidence (logs are `event: request` summaries only).
- **Who/what triggered `/chaos/errors`** (operator, scheduled job, load-generator) is unknown; the `load-generator` pod exists but its request payloads are not shown.
- **Onset time of the 500s** — the log/trace window starts at 11:36:38Z, but `error_ratio_1h` of 10.4% implies failures began earlier in the hour; the exact start is not captured.
- **Relationship (if any) between the probe failures at 11:24Z and the 500s** — probes time out with `context deadline exceeded`, but with `restarts: 0` I cannot tell whether the app was saturated or the health endpoints were themselves affected by the same fault.
- **Whether the control-plane alerts contributed** — no evidence connects `KubeProxyDown`/scheduler/controller-manager or `KubeAPIErrorBudgetBurn` to the `orders-api` errors; cannot conclude either way.
