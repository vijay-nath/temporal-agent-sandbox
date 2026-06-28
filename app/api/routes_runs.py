"""REST surface: create / read / stream / cancel a run.

A stateless shim over Temporal — both GETs read from Temporal, so the API holds no run state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from app.api.auth import verify_token
from app.api.errors import APIError
from app.api.schemas import (
    ErrorDetail,
    RunAccepted,
    RunRequest,
    RunResultModel,
    RunStatusResponse,
    SandboxModel,
    StepModel,
)
from app.config import get_settings
from app.contracts import RunInput, RunResult, RunStatus, SandboxResult
from app.observability.tracing import get_tracer, set_attrs
from app.temporal import policies
from app.temporal.workflows import AgentPipelineWorkflow

log = logging.getLogger(__name__)
tracer = get_tracer()
router = APIRouter()

_TERMINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}
_SSE_EVENT = {
    "COMPLETED": "completed",
    "FAILED": "failed",
    "TIMED_OUT": "timed_out",
    "CANCELLED": "failed",
}


# --- helpers ----------------------------------------------------------------


def _payload_bytes(req: RunRequest) -> bytes:
    return (req.task + json.dumps(req.params.model_dump(), sort_keys=True)).encode("utf-8")


def _workflow_id(req: RunRequest) -> str:
    """Derive the workflow id so identical submissions dedup without a separate store."""
    if req.idempotency_key:
        return f"{req.tenant_id}:{req.idempotency_key}"
    return f"{req.tenant_id}:{hashlib.sha256(_payload_bytes(req)).hexdigest()[:32]}"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _step(s) -> StepModel:
    return StepModel(name=s.name, state=s.state, detail=s.detail)


def _sandbox_model(sb: SandboxResult) -> SandboxModel:
    return SandboxModel(
        runtime=sb.runtime,
        exit_code=sb.exit_code,
        ok=sb.ok,
        failure_reason=sb.failure_reason,
        wall_ms=sb.wall_ms,
        oom_killed=sb.oom_killed,
        wall_clock_exceeded=sb.wall_clock_exceeded,
        truncated=sb.truncated,
        stdout_tail=sb.stdout_tail,
        stderr_tail=sb.stderr_tail,
    )


def _running(run_id: str, rs: RunStatus) -> RunStatusResponse:
    completed = [s.name for s in rs.steps if s.state == "completed"]
    return RunStatusResponse(
        run_id=run_id,
        status="RUNNING",
        stage=rs.stage,
        current_step=rs.stage,
        completed_steps=completed,
        steps=[_step(s) for s in rs.steps],
    )


def _completed(run_id: str, result: RunResult) -> RunStatusResponse:
    sb = _sandbox_model(result.sandbox) if result.sandbox else None
    if result.status == "completed":
        return RunStatusResponse(
            run_id=run_id,
            status="COMPLETED",
            stage="completed",
            completed_steps=["plan", "generate_code", "run_sandbox", "summarize"],
            result=RunResultModel(summary=result.summary, code=result.code, sandbox=sb),
        )
    # workflow returned a 'failed' result (user-code failure)
    return RunStatusResponse(
        run_id=run_id,
        status="FAILED",
        stage="failed",
        result=RunResultModel(summary=result.summary, code=result.code, sandbox=sb),
        error=ErrorDetail(
            code="SANDBOX_FAILURE",
            step="run_sandbox",
            message=result.failure_reason or "run failed",
            retryable=False,
        ),
    )


async def _terminal_failure(run_id: str, st, handle) -> RunStatusResponse:
    mapping = {
        WorkflowExecutionStatus.TIMED_OUT: "TIMED_OUT",
        WorkflowExecutionStatus.CANCELED: "CANCELLED",
        WorkflowExecutionStatus.TERMINATED: "FAILED",
        WorkflowExecutionStatus.FAILED: "FAILED",
    }
    status = mapping.get(st, "FAILED")
    message = status.lower()
    try:
        await handle.result()
    except Exception as e:  # noqa: BLE001 — surface the failure cause as a message
        message = str(e)
    return RunStatusResponse(
        run_id=run_id,
        status=status,
        stage="failed",
        error=ErrorDetail(code=status, message=message, retryable=False),
    )


async def _resolve_status(client: Client, run_id: str) -> RunStatusResponse:
    handle = client.get_workflow_handle(run_id)
    try:
        desc = await handle.describe()
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise APIError(404, "RUN_NOT_FOUND", f"no run with id {run_id}") from e
        raise
    st = desc.status
    if st == WorkflowExecutionStatus.RUNNING:
        rs: RunStatus = await handle.query(AgentPipelineWorkflow.status)
        return _running(run_id, rs)
    if st == WorkflowExecutionStatus.COMPLETED:
        result: RunResult = await handle.result()
        return _completed(run_id, result)
    return await _terminal_failure(run_id, st, handle)


async def _handle_already_started(client: Client, req: RunRequest, wid: str) -> JSONResponse:
    """Idempotent replay (200) vs idempotency conflict (409) on a reused key."""
    expected = hashlib.sha256(_payload_bytes(req)).hexdigest()
    stored = None
    try:
        desc = await client.get_workflow_handle(wid).describe()
        stored = (desc.memo or {}).get("task_sha256")
    except Exception:  # noqa: BLE001 — memo unavailable → treat as idempotent replay
        stored = None
    if stored is not None and stored != expected:
        raise APIError(
            409, "IDEMPOTENCY_CONFLICT", "idempotency key reused with a different payload"
        )
    return JSONResponse(
        status_code=200,
        content=RunAccepted(run_id=wid, status="PENDING", tenant_id=req.tenant_id).model_dump(),
    )


# --- endpoints --------------------------------------------------------------


@router.post("/runs", dependencies=[Depends(verify_token)])
async def create_run(req: RunRequest, request: Request) -> JSONResponse:
    s = get_settings()
    if len(req.task.encode("utf-8")) > s.max_task_bytes:
        raise APIError(413, "VALIDATION_ERROR", "task exceeds maximum size")

    client: Client = request.app.state.temporal
    wid = _workflow_id(req)

    limits = s.resource_limits()
    if req.params.sandbox_timeout_s is not None:  # clamp to policy ceiling
        limits.wall_clock_s = min(req.params.sandbox_timeout_s, s.sandbox_wall_clock_s)

    run_input = RunInput(
        run_id=wid,
        tenant_id=req.tenant_id,
        task=req.task,
        limits=limits,
        sandbox_runtime=s.sandbox_runtime,
    )

    with tracer.start_as_current_span("agent.run") as span:
        set_attrs(span, {"run.id": wid, "tenant.id": req.tenant_id})
        try:
            await client.start_workflow(
                AgentPipelineWorkflow.run,
                run_input,
                id=wid,
                task_queue=s.task_queue,
                run_timeout=policies.WORKFLOW_RUN_TIMEOUT,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                memo={"task_sha256": hashlib.sha256(_payload_bytes(req)).hexdigest()},
            )
        except WorkflowAlreadyStartedError:
            return await _handle_already_started(client, req, wid)
        except RPCError as e:
            if e.status == RPCStatusCode.UNAVAILABLE:
                raise APIError(503, "UPSTREAM_UNAVAILABLE", "temporal is unavailable") from e
            raise

    log.info("run started", extra={"step": "api.create_run", "run_id": wid})
    return JSONResponse(
        status_code=201,
        content=RunAccepted(run_id=wid, status="PENDING", tenant_id=req.tenant_id).model_dump(),
    )


@router.get(
    "/runs/{run_id}", response_model=RunStatusResponse, dependencies=[Depends(verify_token)]
)
async def get_run(run_id: str, request: Request) -> RunStatusResponse:
    return await _resolve_status(request.app.state.temporal, run_id)


@router.get("/runs/{run_id}/stream", dependencies=[Depends(verify_token)])
async def stream_run(run_id: str, request: Request) -> StreamingResponse:
    client: Client = request.app.state.temporal
    handle = client.get_workflow_handle(run_id)
    try:  # validate existence before upgrading to a stream
        await handle.describe()
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise APIError(404, "RUN_NOT_FOUND", f"no run with id {run_id}") from e
        raise

    s = get_settings()
    max_dur = s.workflow_run_timeout_s + 30

    async def gen():
        last: str | None = None
        first = True
        ticks = 0
        elapsed = 0.0
        while True:
            if await request.is_disconnected():
                break
            try:
                resp = await _resolve_status(client, run_id)
            except APIError:
                break
            payload = resp.model_dump()
            if resp.status in _TERMINAL:
                if first:
                    yield _sse("snapshot", payload)
                yield _sse(_SSE_EVENT[resp.status], payload)
                yield _sse("done", {"run_id": run_id})
                break
            cur = json.dumps(payload, sort_keys=True, default=str)
            if first:
                yield _sse("snapshot", payload)
                first, last = False, cur
            elif cur != last:
                yield _sse("step", payload)
                last = cur
            elif ticks % 21 == 0:  # ~every 15s while idle
                yield ":keepalive\n\n"
            await asyncio.sleep(0.7)
            ticks += 1
            elapsed += 0.7
            if elapsed > max_dur:
                yield _sse("done", {"run_id": run_id, "reason": "max_duration"})
                break

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@router.post("/runs/{run_id}/cancel", status_code=202, dependencies=[Depends(verify_token)])
async def cancel_run(run_id: str, request: Request) -> dict:
    client: Client = request.app.state.temporal
    handle = client.get_workflow_handle(run_id)
    try:
        await handle.cancel()
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise APIError(404, "RUN_NOT_FOUND", f"no run with id {run_id}") from e
        raise
    return {"run_id": run_id, "status": "CANCELLING"}
