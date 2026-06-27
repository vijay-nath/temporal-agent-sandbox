"""Deterministic orchestration of the agent pipeline.

No I/O in the workflow — side effects live in the activities — so Temporal can replay it to
reconstruct state. Resilience is per-activity; workflow-level retry is disabled because it
would redo completed work and assign a new run id.
"""

from __future__ import annotations

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.activities.agents import generate_code as generate_code_act
    from app.activities.agents import plan as plan_act
    from app.activities.agents import summarize as summarize_act
    from app.activities.sandbox import run_sandbox as run_sandbox_act
    from app.contracts import (
        RunInput,
        RunResult,
        RunSandboxArgs,
        RunStatus,
        SandboxResult,
        StepStatus,
        SummarizeArgs,
    )
    from app.temporal import policies


@workflow.defn
class AgentPipelineWorkflow:
    def __init__(self) -> None:
        self._stage: str = "queued"
        self._steps: list[StepStatus] = []

    @workflow.run
    async def run(self, inp: RunInput) -> RunResult:
        plan = await self._step(
            "plan", plan_act, inp.task, policies.AGENT_START_TO_CLOSE, policies.AGENT_RETRY
        )
        code = await self._step(
            "generate_code", generate_code_act, plan,
            policies.AGENT_START_TO_CLOSE, policies.AGENT_RETRY,
        )
        sbx = await self._run_sandbox(inp, code)

        # decide terminal status from the activity's result
        if not sbx.ok:
            self._stage = "failed"
            return RunResult.failed(
                inp.run_id, reason=sbx.failure_reason or "sandbox_failed", sandbox=sbx, code=code
            )

        summary = await self._step(
            "summarize", summarize_act, SummarizeArgs(plan=plan, sandbox=sbx),
            policies.AGENT_START_TO_CLOSE, policies.AGENT_RETRY,
        )
        self._stage = "completed"
        return RunResult.completed(inp.run_id, summary=summary, code=code, sandbox=sbx)

    @workflow.query
    def status(self) -> RunStatus:
        """Point-in-time status."""
        return RunStatus(
            run_id=workflow.info().workflow_id, stage=self._stage, steps=list(self._steps)
        )

    # --- helpers ------------------------------------------------------------
    async def _step(self, name, activity, arg, start_to_close, retry):
        self._stage = name
        step = StepStatus(name=name, state="running")
        self._steps.append(step)
        try:
            result = await workflow.execute_activity(
                activity,
                arg,
                start_to_close_timeout=start_to_close,
                schedule_to_start_timeout=policies.SCHEDULE_TO_START,
                retry_policy=retry,
            )
            step.state = "completed"
            return result
        except Exception:
            step.state = "failed"
            raise

    async def _run_sandbox(self, inp: RunInput, code: str) -> SandboxResult:
        self._stage = "run_sandbox"
        step = StepStatus(name="run_sandbox", state="running")
        self._steps.append(step)
        args = RunSandboxArgs(
            run_id=inp.run_id,
            tenant_id=inp.tenant_id,
            code=code,
            limits=inp.limits,
            runtime=inp.sandbox_runtime,
        )
        try:
            result: SandboxResult = await workflow.execute_activity(
                run_sandbox_act,
                args,
                start_to_close_timeout=policies.SANDBOX_START_TO_CLOSE,
                schedule_to_start_timeout=policies.SCHEDULE_TO_START,
                heartbeat_timeout=policies.SANDBOX_HEARTBEAT,
                retry_policy=policies.SANDBOX_RETRY,
            )
        except Exception:
            step.state = "failed"
            raise
        # activity succeeded; the user-code outcome is in the result
        step.state = "completed" if result.ok else "failed"
        if not result.ok:
            step.detail = result.failure_reason
        return result
