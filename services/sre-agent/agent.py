"""AI SRE agent: deterministic evidence collection + one Claude call -> RCA markdown.

Collectors never call the LLM. They gather signals from Alertmanager, Prometheus,
Loki, Tempo, and the Kubernetes API, truncate them to a token budget, and hand a
single evidence bundle to Claude, which writes the structured RCA. The RCA is
printed to stdout between markers; all diagnostics go to stderr so the demo script
can extract just the report.
"""
import json
import os
import sys
import time

import requests

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "8000"))

PROM = os.getenv("PROM_URL", "http://kube-prometheus-stack-prometheus.monitoring:9090")
ALERT = os.getenv("ALERT_URL", "http://kube-prometheus-stack-alertmanager.monitoring:9093")
LOKI = os.getenv("LOKI_URL", "http://loki.monitoring:3100")
TEMPO = os.getenv("TEMPO_URL", "http://tempo.monitoring:3100")
NS = os.getenv("APP_NAMESPACE", "apps")
SERVICE = os.getenv("SERVICE", "orders-api")

# evidence caps — the token budget is enforced by truncation, not by the model
LOG_MAX_LINES = int(os.getenv("LOG_MAX_LINES", "200"))
LOG_MAX_CHARS = 400
TRACE_MAX = 8
EVENT_MAX = 40
SLO_BUDGET = 0.005  # 1 - 99.5%

RCA_START = "===RCA-START==="
RCA_END = "===RCA-END==="


def log(msg, **kv):
    extra = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"[collector] {msg} {extra}".rstrip(), file=sys.stderr, flush=True)


def _get(url, **kw):
    kw.setdefault("timeout", 15)
    r = requests.get(url, **kw)
    r.raise_for_status()
    return r


# --- Prometheus -------------------------------------------------------------
def prom_query(expr):
    r = _get(f"{PROM}/api/v1/query", params={"query": expr})
    res = r.json()["data"]["result"]
    if not res:
        return None
    return round(float(res[0]["value"][1]), 5)


def collect_prometheus():
    sel = f'{{job="{SERVICE}", path="/api/orders"}}'
    hist = f'sum by (le) (rate(http_request_duration_seconds_bucket{sel}[5m]))'
    err5m = prom_query(f"{SERVICE.replace('-', '_')}:error_ratio:rate5m")
    err1h = prom_query(f"{SERVICE.replace('-', '_')}:error_ratio:rate1h")
    out = {
        "request_rate_5m": prom_query(f"{SERVICE.replace('-', '_')}:requests:rate5m"),
        "error_ratio_5m": err5m,
        "error_ratio_1h": err1h,
        "burn_rate_5m": round(err5m / SLO_BUDGET, 2) if err5m is not None else None,
        "burn_rate_1h": round(err1h / SLO_BUDGET, 2) if err1h is not None else None,
        "latency_p50_s": prom_query(f"histogram_quantile(0.50, {hist})"),
        "latency_p95_s": prom_query(f"histogram_quantile(0.95, {hist})"),
        "latency_p99_s": prom_query(f"histogram_quantile(0.99, {hist})"),
        "availability_ratio_30d": prom_query(f"{SERVICE.replace('-', '_')}:availability:ratio30d"),
        "error_budget_remaining_30d": prom_query(
            f"{SERVICE.replace('-', '_')}:error_budget_remaining:ratio30d"
        ),
        "pod_restarts_1h": prom_query(
            f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{NS}"}}[1h]))'
        ),
    }
    log("prometheus", error_ratio_5m=out["error_ratio_5m"], burn_1h=out["burn_rate_1h"])
    return out


# --- Alertmanager -----------------------------------------------------------
def collect_alerts():
    r = _get(f"{ALERT}/api/v2/alerts", params={"active": "true", "silenced": "false"})
    alerts = []
    for a in r.json():
        labels = a.get("labels", {})
        ann = a.get("annotations", {})
        alerts.append({
            "alertname": labels.get("alertname"),
            "severity": labels.get("severity"),
            "state": a.get("status", {}).get("state"),
            "activeAt": a.get("startsAt"),
            "summary": ann.get("summary"),
            "description": (ann.get("description") or "").strip(),
        })
    log("alertmanager", firing=len(alerts))
    return alerts


# --- Loki -------------------------------------------------------------------
def collect_logs():
    end = int(time.time() * 1e9)
    start = end - int(3600 * 1e9)
    query = f'{{service_name="{SERVICE}"}} | json | status >= 500'
    r = _get(f"{LOKI}/loki/api/v1/query_range", params={
        "query": query, "start": start, "end": end,
        "limit": LOG_MAX_LINES, "direction": "backward",
    })
    lines = []
    for stream in r.json()["data"]["result"]:
        for ts, line in stream["values"]:
            lines.append(line[:LOG_MAX_CHARS])
    lines = lines[:LOG_MAX_LINES]
    log("loki", err_log_lines=len(lines))
    return {"query": query, "count": len(lines), "lines": lines}


# --- Tempo ------------------------------------------------------------------
def _tempo_search(traceql):
    r = _get(f"{TEMPO}/api/search", params={"q": traceql, "limit": TRACE_MAX})
    out = []
    for t in r.json().get("traces", []):
        out.append({
            "traceID": t.get("traceID"),
            "rootTraceName": t.get("rootTraceName"),
            "durationMs": t.get("durationMs"),
        })
    return out


def _tempo_spans(trace_id):
    r = _get(f"{TEMPO}/api/traces/{trace_id}", headers={"Accept": "application/json"})
    spans = []
    for b in r.json().get("batches", []):
        for ss in b.get("scopeSpans", []):
            for s in ss.get("spans", []):
                spans.append({
                    "name": s.get("name"),
                    "status": s.get("status", {}).get("code"),
                    "durationMs": round(
                        (int(s.get("endTimeUnixNano", 0)) - int(s.get("startTimeUnixNano", 0))) / 1e6, 2
                    ),
                })
    return spans


def collect_traces():
    slow = _tempo_search(f'{{ resource.service.name="{SERVICE}" && duration > 300ms }}')
    errored = _tempo_search(f'{{ resource.service.name="{SERVICE}" && status = error }}')
    sample_spans = {}
    for t in (errored[:1] + slow[:1]):
        tid = t["traceID"]
        if tid and tid not in sample_spans:
            try:
                sample_spans[tid] = _tempo_spans(tid)
            except Exception as e:  # noqa: BLE001
                sample_spans[tid] = {"error": str(e)}
    log("tempo", slow=len(slow), errored=len(errored))
    return {"slow_traces": slow, "errored_traces": errored, "sample_span_breakdown": sample_spans}


# --- Kubernetes (read-only: get/list pods + events) -------------------------
def collect_kubernetes():
    from kubernetes import client, config
    config.load_incluster_config()
    v1 = client.CoreV1Api()

    pods = []
    for p in v1.list_namespaced_pod(NS).items:
        for cs in (p.status.container_statuses or []):
            term = (cs.last_state.terminated if cs.last_state else None)
            pods.append({
                "pod": p.metadata.name,
                "container": cs.name,
                "restarts": cs.restart_count,
                "last_terminated": None if not term else {
                    "reason": term.reason, "exitCode": term.exit_code,
                    "finishedAt": str(term.finished_at),
                },
            })

    events = []
    for e in v1.list_namespaced_event(NS).items:
        if e.type == "Normal":
            continue  # warnings are the interesting ones
        events.append({
            "type": e.type, "reason": e.reason,
            "object": f"{e.involved_object.kind}/{e.involved_object.name}",
            "count": e.count, "message": (e.message or "")[:200],
            "lastTimestamp": str(e.last_timestamp),
        })
    events = events[:EVENT_MAX]
    log("kubernetes", pods=len(pods), warning_events=len(events))
    return {"pods": pods, "warning_events": events}


def safe(name, fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log(f"{name} FAILED", error=str(e))
        return {"error": str(e)}


SYSTEM = """You are an on-call Site Reliability Engineer performing a root-cause \
analysis. You are given a machine-collected evidence bundle (firing alerts, \
Prometheus metrics, Loki error logs, Tempo traces, and Kubernetes state) for the \
`orders-api` service. The SLO is 99.5% availability on POST/GET /api/orders \
(error budget = 0.5%); a multiwindow burn-rate alert pages at 14.4x burn.

Write a root-cause analysis in GitHub-flavored markdown. Ground EVERY claim in the \
provided evidence — quote specific metric values, log lines, and trace IDs. Do not \
invent data. If the evidence is insufficient to conclude something, say so plainly \
rather than guessing.

Use exactly these sections (H2 headers):
## Summary            — 2-3 sentences: what is happening and impact.
## Timeline           — ordered events with the timestamps present in the evidence.
## Root Cause         — the most likely cause, with an explicit **Confidence: High/Medium/Low** and why.
## Evidence           — bullet list citing specific metrics, log lines (with trace_id), and trace IDs.
## Blast Radius       — who/what is affected and how broadly.
## Remediation        — **Immediate:** and **Preventive:** subsections.
## What I could not determine — gaps, missing signals, or ambiguities the evidence left open.

Start with a top-level `# RCA: orders-api` title. Be concise and specific."""


def call_claude(bundle_text):
    import anthropic
    client = anthropic.Anthropic()
    base = dict(
        model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM,
        messages=[{"role": "user", "content": bundle_text}],
    )
    try:
        resp = client.messages.create(
            thinking={"type": "adaptive"}, output_config={"effort": "high"}, **base
        )
    except TypeError:  # older SDK without adaptive-thinking / effort kwargs
        resp = client.messages.create(**base)
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def main():
    log("collecting evidence", service=SERVICE, namespace=NS)
    bundle = {
        "service": SERVICE,
        "namespace": NS,
        "slo": {"availability_target": 0.995, "error_budget": SLO_BUDGET},
        "firing_alerts": safe("alertmanager", collect_alerts),
        "metrics": safe("prometheus", collect_prometheus),
        "error_logs": safe("loki", collect_logs),
        "traces": safe("tempo", collect_traces),
        "kubernetes": safe("kubernetes", collect_kubernetes),
    }
    bundle_text = (
        "Evidence bundle for the current incident:\n```json\n"
        + json.dumps(bundle, indent=2, default=str)
        + "\n```"
    )
    log("evidence bundle built", chars=len(bundle_text))

    rca = call_claude(bundle_text)

    print(RCA_START)
    print(rca)
    print(RCA_END)


if __name__ == "__main__":
    main()
