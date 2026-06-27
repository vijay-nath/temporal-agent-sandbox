"""Worker process: hosts the workflow and activities and polls the task queue. Stateless —
state lives in Temporal — and refuses to start if the configured sandbox runtime is
unavailable."""

from __future__ import annotations

import asyncio
import logging

from temporalio.worker import Worker

from app.activities.agents import generate_code, plan, summarize
from app.activities.sandbox import run_sandbox
from app.config import get_settings
from app.observability.logging import configure_logging
from app.observability.tracing import setup_tracing
from app.sandbox.runner import get_runner
from app.temporal.client import create_client
from app.temporal.workflows import AgentPipelineWorkflow

log = logging.getLogger(__name__)


async def main() -> None:
    s = get_settings()
    configure_logging(f"{s.otel_service_name}-worker", s.log_level)
    setup_tracing(f"{s.otel_service_name}-worker", s.otel_exporter_otlp_endpoint)

    # fail closed before serving any work
    await get_runner(s.sandbox_runtime).preflight()

    client = await create_client()
    worker = Worker(
        client,
        task_queue=s.task_queue,
        workflows=[AgentPipelineWorkflow],
        activities=[plan, generate_code, summarize, run_sandbox],
    )
    log.info(
        "worker starting",
        extra={"task_queue": s.task_queue, "sandbox_runtime": s.sandbox_runtime},
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
