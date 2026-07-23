import logging
import os
import random
import time

import structlog
from fastapi import FastAPI, HTTPException, Query, Request, Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "orders-api")
# OTLP/gRPC straight to Tempo — no OTel Collector in the local path (see README).
OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo.monitoring.svc.cluster.local:4317"
)

provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(SERVICE_NAME)


def add_trace_context(_, __, event_dict):
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_trace_context,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
log = structlog.get_logger()

REQUESTS = Counter(
    "http_requests_total", "Total HTTP requests", ["path", "method", "status"]
)
DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["path", "method"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# in-memory, per-replica chaos knobs
chaos = {"latency_ms": 0, "error_rate": 0.0}

app = FastAPI(title="orders-api")


@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    REQUESTS.labels(path, request.method, str(response.status_code)).inc()
    DURATION.labels(path, request.method).observe(elapsed)
    log.info(
        "request",
        path=path,
        method=request.method,
        status=response.status_code,
        duration_ms=round(elapsed * 1000, 2),
    )
    return response


def _apply_chaos():
    if chaos["latency_ms"] > 0:
        time.sleep(chaos["latency_ms"] / 1000)
    if chaos["error_rate"] > 0 and random.random() < chaos["error_rate"]:
        raise HTTPException(status_code=500, detail="chaos: injected error")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    return {"status": "ready"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/orders")
async def list_orders():
    _apply_chaos()
    with tracer.start_as_current_span("db.query.orders") as span:
        span.set_attribute("db.system", "postgresql")
        span.set_attribute("db.statement", "SELECT * FROM orders LIMIT 3")
        time.sleep(random.uniform(0.005, 0.020))  # fake db round-trip
    return {"orders": [{"id": i, "item": f"widget-{i}"} for i in range(1, 4)]}


@app.post("/api/orders")
async def create_order():
    _apply_chaos()
    if random.random() < 0.005:  # baked-in 0.5% failure rate
        raise HTTPException(status_code=500, detail="internal error")
    with tracer.start_as_current_span("db.insert.order") as span:
        span.set_attribute("db.system", "postgresql")
        time.sleep(random.uniform(0.005, 0.020))
    return Response(
        content='{"status":"created"}', media_type="application/json", status_code=201
    )


@app.post("/chaos/latency")
async def chaos_latency(ms: int = Query(0, ge=0, le=10000)):
    chaos["latency_ms"] = ms
    log.warning("chaos.latency", ms=ms)
    return {"latency_ms": ms}


@app.post("/chaos/errors")
async def chaos_errors(rate: float = Query(0.0, ge=0.0, le=1.0)):
    chaos["error_rate"] = rate
    log.warning("chaos.errors", rate=rate)
    return {"error_rate": rate}


@app.post("/chaos/reset")
async def chaos_reset():
    chaos.update(latency_ms=0, error_rate=0.0)
    log.warning("chaos.reset")
    return {"latency_ms": 0, "error_rate": 0.0}


FastAPIInstrumentor.instrument_app(app)
