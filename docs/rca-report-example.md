# RCA: orders-api

## Summary
`orders-api` is returning HTTP 500 on ~75.7% of `/api/orders` requests (`error_ratio_5m` and `error_ratio_1h` both `0.75701`), burning the 99.5% error budget at **151.4x** — far above the 14.4x page threshold. The 30-day availability has collapsed to `0.24299` and the error budget is fully exhausted (`error_budget_remaining_30d: -150.40179`). Both GET and POST to `/api/orders` are affected across both service replicas.

## Timeline
- **2026-07-23 17:51:43–17:51:44** — Both pods refuse connections on their probes: `Liveness probe failed ... dial tcp 10.42.0.31:8000: connect: connection refused` (Pod `orders-api-84dbfcdd9b-rxdgs`) and the equivalent readiness failure on `10.42.0.30:8000` (Pod `...-c9gcw`).
- **2026-07-23 17:53:52–17:55:34** — Probe failures shift to timeouts: `Liveness probe failed ... context deadline exceeded` (count 6 on `c9gcw`, last 17:55:34) and matching readiness timeouts on both pods.
- **2026-07-23 18:11:11.295Z** — `Watchdog` alert active (pipeline health only, not incident-related).
- **2026-07-23 18:11:42.210Z** — Earliest 500 in the log sample (trace_id `2b5dd53b840e6e0e14ea3e304bc968c8`).
- **2026-07-23 18:14:32.780Z** — `OrdersApiErrorBudgetFastBurn` fires (critical): "5xx ratio on /api/orders exceeds 7.2% over both the 1h and 5m windows."
- **2026-07-23 18:14:42.371Z** — Latest 500 in the log sample (trace_id `cc02439ca6cfb1e18c16f778a672a708`).

## Root Cause
The `/api/orders` request handler is failing on an outbound/downstream call, returning 500 to clients. Trace `994988a9ec2b50ed28a614eb6c1673fd` shows the root span `GET /api/orders` with `status: STATUS_CODE_ERROR` (503.8 ms) and a child span `GET /api/orders http send` also `STATUS_CODE_ERROR` (0.05 ms), with additional `http send` child spans present — consistent with a failed dependency call inside the handler. Every failing request completes in a tightly clustered ~500 ms band (e.g., `500.54`, `500.60`, `501.62`, up to `526`), which points to a fixed timeout/delay on that dependency call rather than random failures. Latency percentiles pinned just under this ceiling (`p95 0.9682s`, `p99 0.99364s`) reinforce a bounded-wait-then-fail pattern.

**Confidence: Medium.** The trace span structure and uniform ~500 ms failure duration strongly indicate a handler-level dependency failure, but the evidence contains no error message text, no named downstream service, and no dependency-side metrics/logs, so the exact failing component and reason (timeout vs. injected fault vs. bad config) cannot be pinned down.

## Evidence
- **Metrics:** `error_ratio_5m: 0.75701`, `error_ratio_1h: 0.75701`, `burn_rate_5m/1h: 151.4`, `availability_ratio_30d: 0.24299`, `error_budget_remaining_30d: -150.40179`, `request_rate_5m: 0.45112`, `pod_restarts_1h: 0.0`.
- **Alert:** `OrdersApiErrorBudgetFastBurn` (critical, activeAt `2026-07-23T18:14:32.780Z`): ">14.4x" burn, budget exhausted "in ~2 days."
- **Logs (Loki, 118 matches for `status >= 500`):** all sampled lines are `status: 500` on `/api/orders`, both methods, e.g.:
  - `GET` `500` `duration_ms 522.95` trace_id `8339d192a8dc32d4b904a10708022bb3` @ 18:13:50
  - `POST` `500` `duration_ms 503.07` trace_id `20f29d5cf12912a63a317ca2313e8531` @ 18:12:57
  - `GET` `500` `duration_ms 500.54` trace_id `71c29fb424223f8699e3ef1f1776a119` @ 18:12:23
- **Traces:** errored root spans `GET /api/orders` / `POST /api/orders` (e.g., `994988a9ec2b50ed28a614eb6c1673fd`, `572da1b5da5379a676139f699a8172ee`, `2ff506d2c79bd1af9eb422ba00215f46`); span breakdown for `994988a9...` shows root `STATUS_CODE_ERROR` at 503.8 ms with a child `http send` `STATUS_CODE_ERROR`.
- **Kubernetes:** Warning events on both `orders-api-84dbfcdd9b-c9gcw` and `-rxdgs` — connection-refused then `context deadline exceeded` on `/healthz` and `/readyz` (17:51–17:55). Note `ClusterIPNotAllocated` on `Service/orders-api` ("10.43.216.101 is not allocated; repairing"), timestamp `None`.

## Blast Radius
- Both `orders-api` replicas (`orders-api-84dbfcdd9b-c9gcw` and `-rxdgs`) — the entire serving fleet (2 pods).
- Both endpoints affected: GET and POST `/api/orders` (500s observed for both methods).
- ~75.7% of all `/api/orders` traffic is failing; at the observed request rate of `0.45112` rps this is a low-volume (likely test/load-generator, per pod `load-generator-68c64b46bf-dsfxl`) environment, but proportionally the service is effectively unusable and the 30-day SLO is already blown (availability `24.3%`, budget `-150`).

## Remediation
**Immediate:**
- Identify the downstream call in the `/api/orders` handler surfaced by the errored `http send` child spans (start from trace `994988a9ec2b50ed28a614eb6c1673fd`) and verify that dependency's health/connectivity.
- Investigate the `Service/orders-api` `ClusterIPNotAllocated` event ("10.43.216.101 is not allocated; repairing") to rule out a networking/service-routing fault contributing to the failed calls.
- If a recent deploy/config change to `orders-api` or its dependency preceded 18:11, roll it back (deploy history not in this bundle — see gaps).

**Preventive:**
- Add explicit error logging (message/exception + dependency name) on the 500 path; current logs record only `status: 500` with no cause.
- Add circuit breaking / bounded retries and a dependency-availability SLO/alert so downstream failures are detected before they saturate `/api/orders`.
- Tune probes: the 17:51–17:55 liveness/readiness timeouts did not lead to restarts (`pod_restarts_1h: 0.0`); confirm probe thresholds and whether unhealthy pods should be restarted or removed from endpoints.

## What I could not determine
- **The specific failing dependency and error reason** — logs carry no error text and traces name only a generic `http send` child span; no downstream service, status code, or exception is provided.
- **Whether a deploy/config change triggered this** — no deployment, image-tag, or change-event data is in the bundle.
- **The link between the 17:51–17:55 probe failures and the 18:11+ 500s** — there is a ~15-minute gap and no restarts, so it is unclear whether these are the same fault or two distinct events.
- **True production impact** — request rate is very low (`0.45` rps) with a `load-generator` pod present, suggesting a test environment; real user impact cannot be confirmed from this data.
- **The `ClusterIPNotAllocated` event significance** — it has a null count and `None` timestamp, so it may be historical/benign rather than active.
