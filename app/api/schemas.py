"""Pydantic request/response models for the API edge (validation + stable wire shapes)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunParams(BaseModel):
    """Optional per-run overrides. Server-clamped to the configured ceilings."""

    # Requested wall-clock; the server clamps it to SANDBOX_WALL_CLOCK_S, so the effective
    # max is the configured limit.
    sandbox_timeout_s: int | None = Field(default=None, ge=1, le=120)


class RunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8192)
    params: RunParams = Field(default_factory=RunParams)
    # In production tenant_id comes from verified token claims, not the body.
    tenant_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    idempotency_key: str | None = Field(default=None, max_length=128)


class RunAccepted(BaseModel):
    run_id: str
    status: str
    tenant_id: str


class StepModel(BaseModel):
    name: str
    state: str
    detail: str | None = None


class SandboxModel(BaseModel):
    runtime: str
    exit_code: int
    ok: bool
    failure_reason: str | None = None
    wall_ms: int
    oom_killed: bool = False
    wall_clock_exceeded: bool = False
    truncated: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""


class RunResultModel(BaseModel):
    summary: str | None = None
    code: str | None = None
    sandbox: SandboxModel | None = None


class ErrorDetail(BaseModel):
    code: str
    step: str | None = None
    message: str
    retryable: bool = False


class RunStatusResponse(BaseModel):
    run_id: str
    status: str  # PENDING|RUNNING|COMPLETED|FAILED|TIMED_OUT|CANCELLED
    stage: str
    current_step: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    steps: list[StepModel] = Field(default_factory=list)
    result: RunResultModel | None = None
    error: ErrorDetail | None = None


# The non-2xx error envelope is built inline in main.py (_envelope).
