# orders-api

Small FastAPI service that emits all three signals, used to exercise the platform.

## Endpoints
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/healthz` | liveness |
| GET | `/readyz` | readiness |
| GET | `/api/orders` | lists orders; child span `db.query.orders` sleeps 5–20ms |
| POST | `/api/orders` | creates an order; **0.5% baked-in 500s** |
| GET | `/metrics` | Prometheus exposition |
| POST | `/chaos/latency?ms=` | inject latency on `/api/orders` (per replica) |
| POST | `/chaos/errors?rate=` | inject 5xx rate 0.0–1.0 (per replica) |
| POST | `/chaos/reset` | clear chaos |

## Signals
- **Metrics** — `prometheus_client`: `http_requests_total{path,method,status}` and
  `http_request_duration_seconds` histogram, scraped via a `ServiceMonitor`.
- **Traces** — OTel auto-instrumentation (`FastAPIInstrumentor`) exporting OTLP/gRPC
  **straight to Tempo** (`tempo.monitoring:4317`). No OTel Collector runs locally; in
  prod this would be a Collector DaemonSet doing batching + tail sampling.
- **Logs** — `structlog` JSON to stdout with `trace_id`/`span_id` injected from the active
  span, shipped to Loki by Alloy. Grafana's Loki datasource derives a Tempo link from `trace_id`.

## Notes
- **No CPU limit** — requests (`50m`) drive scheduling; a CPU limit would only add
  throttling and tail latency under load. Memory is limited (OOM is the real risk).
- Chaos state is in-memory **per replica**; the demo drives it against every pod.
