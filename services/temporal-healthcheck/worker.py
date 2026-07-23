"""Operational health-check: a scheduled Temporal workflow that probes orders-api.

The worker registers the workflow + activity, ensures a Schedule exists that runs
the workflow every INTERVAL_SECONDS, then serves the task queue. The activity does
the actual HTTP GET to orders-api /healthz (cross-namespace); the workflow stays
deterministic and just invokes it.
"""
import asyncio
import os
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
)
from temporalio.worker import Worker

TEMPORAL = os.getenv("TEMPORAL_ADDRESS", "temporal-frontend.temporal.svc.cluster.local:7233")
NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TARGET = os.getenv("TARGET_URL", "http://orders-api.apps.svc.cluster.local/healthz")
TASK_QUEUE = "orders-api-healthcheck"
INTERVAL_SECONDS = int(os.getenv("INTERVAL_SECONDS", "60"))


@activity.defn
def probe_healthz() -> dict:
    import requests  # inside the activity — never imported into the workflow sandbox

    r = requests.get(TARGET, timeout=5)
    return {"url": TARGET, "status_code": r.status_code, "healthy": r.ok}


@workflow.defn
class HealthCheckWorkflow:
    @workflow.run
    async def run(self) -> dict:
        return await workflow.execute_activity(
            probe_healthz,
            start_to_close_timeout=timedelta(seconds=10),
        )


async def connect() -> Client:
    last = None
    for _ in range(60):  # frontend / default namespace may still be coming up
        try:
            return await Client.connect(TEMPORAL, namespace=NAMESPACE)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"waiting for temporal at {TEMPORAL}: {e}", flush=True)
            await asyncio.sleep(5)
    raise last


async def ensure_schedule(client: Client) -> None:
    try:
        await client.create_schedule(
            "orders-api-healthcheck",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    HealthCheckWorkflow.run,
                    id="orders-api-healthcheck",
                    task_queue=TASK_QUEUE,
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=timedelta(seconds=INTERVAL_SECONDS))]
                ),
            ),
        )
        print("schedule created", flush=True)
    except Exception as e:  # noqa: BLE001 — already exists on restart
        print(f"schedule not created (likely exists): {e}", flush=True)


async def main() -> None:
    client = await connect()
    await ensure_schedule(client)
    print(f"worker serving task queue '{TASK_QUEUE}'", flush=True)
    with ThreadPoolExecutor(max_workers=4) as executor:
        await Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[HealthCheckWorkflow],
            activities=[probe_healthz],
            activity_executor=executor,
        ).run()


if __name__ == "__main__":
    asyncio.run(main())
