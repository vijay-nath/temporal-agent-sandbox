"""Temporal activities for the agent steps. They own telemetry and delegate the transform to
app.agents.pipeline. run_id equals the workflow id, so it is read from activity.info()."""

from __future__ import annotations

import hashlib
import logging

from temporalio import activity

from app.agents import pipeline
from app.config import get_settings
from app.contracts import Plan, SummarizeArgs
from app.observability.logging import run_id_var, tenant_id_var
from app.observability.tracing import get_tracer, set_attrs, set_observation_type

log = logging.getLogger(__name__)
tracer = get_tracer()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bind() -> tuple[str, str | None]:
    info = activity.info()
    run_id = info.workflow_id
    tenant_id = run_id.split(":", 1)[0] if ":" in run_id else None
    run_id_var.set(run_id)
    tenant_id_var.set(tenant_id)
    return run_id, tenant_id


def _baseline(span, run_id: str, tenant_id: str | None, step: str) -> None:
    set_observation_type(span, "span")
    set_attrs(
        span,
        {
            "run.id": run_id,
            "tenant.id": tenant_id,
            "step.name": step,
            "attempt": activity.info().attempt,
        },
    )


@activity.defn
async def plan(task: str) -> Plan:
    run_id, tenant_id = _bind()
    with tracer.start_as_current_span("activity.plan") as span:
        _baseline(span, run_id, tenant_id, "plan")
        set_attrs(span, {"input.bytes": len(task), "input.sha256": _sha(task)})
        result = pipeline.plan(task)
        set_attrs(span, {"plan.directive": result.directive})
        log.info("planned", extra={"step": "plan", "directive": result.directive})
        return result


@activity.defn
async def generate_code(plan_: Plan) -> str:
    run_id, tenant_id = _bind()
    settings = get_settings()
    with tracer.start_as_current_span("activity.generate_code") as span:
        _baseline(span, run_id, tenant_id, "generate_code")
        code = pipeline.generate_code(plan_)
        set_attrs(span, {"code.sha256": _sha(code), "output.bytes": len(code)})
        if settings.trace_payloads:
            set_attrs(span, {"output.preview": code[:2000]})
        log.info("generated code", extra={"step": "generate_code", "bytes": len(code)})
        return code


@activity.defn
async def summarize(args: SummarizeArgs) -> str:
    run_id, tenant_id = _bind()
    with tracer.start_as_current_span("activity.summarize") as span:
        _baseline(span, run_id, tenant_id, "summarize")
        summary = pipeline.summarize(args)
        set_attrs(span, {"output.bytes": len(summary)})
        log.info("summarized", extra={"step": "summarize"})
        return summary
