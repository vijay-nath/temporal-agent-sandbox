"""Workflow determinism / orchestration tests using Temporal's test environment.

Activities are mocked (deterministic, no Docker) so this exercises the WORKFLOW logic:
- the happy path completes;
- a non-zero sandbox outcome makes the WORKFLOW return a 'failed' RunResult WITHOUT
  invoking summarize (proving the workflow — not the activity — decides terminal status);
- the recorded history replays deterministically (catches non-determinism regressions).

Needs the Temporal test server (downloaded on first run); skipped gracefully if unavailable.
"""

from __future__ import annotations

import uuid

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.contracts import Plan, ResourceLimits, RunInput, SandboxResult
from app.temporal.workflows import AgentPipelineWorkflow

TASK_QUEUE = "test-queue"
_summarize_calls: list[int] = []


@activity.defn(name="plan")
async def plan_mock(task: str) -> Plan:
    return Plan(task=task, directive="normal", steps=["a", "b", "c", "d"])


@activity.defn(name="generate_code")
async def generate_code_mock(plan: Plan) -> str:
    return "print('hello')"


@activity.defn(name="summarize")
async def summarize_mock(args) -> str:
    _summarize_calls.append(1)
    return "summary"


def _ok_sandbox() -> SandboxResult:
    return SandboxResult(runtime="runsc", exit_code=0, stdout_tail="{}", stderr_tail="", wall_ms=5)


def _bad_sandbox() -> SandboxResult:
    return SandboxResult(
        runtime="runsc", exit_code=3, stdout_tail="", stderr_tail="boom", wall_ms=5
    )


def _make_sandbox_mock(result: SandboxResult):
    @activity.defn(name="run_sandbox")
    async def run_sandbox_mock(args) -> SandboxResult:
        return result

    return run_sandbox_mock


@pytest.fixture
async def env():
    try:
        e = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Temporal test server unavailable: {exc}")
    async with e:
        yield e


def _run_input(run_id: str) -> RunInput:
    return RunInput(
        run_id=run_id, tenant_id="demo", task="hello",
        limits=ResourceLimits(), sandbox_runtime="runsc",
    )


async def test_happy_path_completes(env):
    _summarize_calls.clear()
    wid = f"demo:{uuid.uuid4().hex}"
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[AgentPipelineWorkflow],
        activities=[
            plan_mock, generate_code_mock, _make_sandbox_mock(_ok_sandbox()), summarize_mock
        ],
    ):
        result = await env.client.execute_workflow(
            AgentPipelineWorkflow.run, _run_input(wid), id=wid, task_queue=TASK_QUEUE
        )
    assert result.status == "completed"
    assert result.summary == "summary"
    assert len(_summarize_calls) == 1


async def test_sandbox_failure_makes_workflow_fail_without_summarize(env):
    _summarize_calls.clear()
    wid = f"demo:{uuid.uuid4().hex}"
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[AgentPipelineWorkflow],
        activities=[
            plan_mock, generate_code_mock, _make_sandbox_mock(_bad_sandbox()), summarize_mock
        ],
    ):
        result = await env.client.execute_workflow(
            AgentPipelineWorkflow.run, _run_input(wid), id=wid, task_queue=TASK_QUEUE
        )
    assert result.status == "failed"
    assert result.failure_reason == "nonzero_exit:3"
    assert _summarize_calls == []  # workflow short-circuits; summarize never runs
