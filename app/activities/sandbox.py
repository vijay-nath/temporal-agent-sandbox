"""The run_sandbox activity: executes untrusted code across the trust boundary.

User-code failure (non-zero exit / OOM / wall-clock) is a normal outcome — the activity
returns a SandboxResult and the workflow decides the run's status. Only infrastructure faults
raise: SandboxInfraError (retryable) or SandboxPolicyViolation (non-retryable). The activity
heartbeats while the sandbox runs and tears it down on cancellation.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.config import get_settings
from app.contracts import RunSandboxArgs, SandboxResult
from app.observability.logging import run_id_var, tenant_id_var
from app.observability.tracing import get_tracer, record_error, set_attrs, set_observation_type
from app.sandbox.runner import SandboxInfraError, SandboxPolicyViolation, get_runner

log = logging.getLogger(__name__)
tracer = get_tracer()


@activity.defn
async def run_sandbox(args: RunSandboxArgs) -> SandboxResult:
    run_id_var.set(args.run_id)
    tenant_id_var.set(args.tenant_id)
    settings = get_settings()
    attempt = activity.info().attempt

    if len(args.code) > settings.max_code_bytes:
        raise ApplicationError(
            f"generated code exceeds {settings.max_code_bytes} bytes",
            type="PayloadTooLarge",
            non_retryable=True,
        )

    runner = get_runner(args.runtime)

    def _hb(detail: dict) -> None:
        activity.heartbeat(detail)

    with tracer.start_as_current_span("activity.run_sandbox") as act_span:
        set_observation_type(act_span, "span")
        set_attrs(
            act_span,
            {
                "run.id": args.run_id,
                "tenant.id": args.tenant_id,
                "step.name": "run_sandbox",
                "attempt": attempt,
                "sandbox.runtime": args.runtime,
                "code.sha256": hashlib.sha256(args.code.encode()).hexdigest(),
            },
        )
        try:
            with tracer.start_as_current_span("sandbox.exec") as exec_span:
                set_observation_type(exec_span, "tool")  # untrusted execution
                set_attrs(exec_span, {"sandbox.runtime": args.runtime})
                result = await runner.run(args, attempt=attempt, heartbeat=_hb)
                set_attrs(
                    exec_span,
                    {
                        "sandbox.exit_code": result.exit_code,
                        "sandbox.wall_ms": result.wall_ms,
                        "sandbox.oom": result.oom_killed,
                        "sandbox.wall_clock_exceeded": result.wall_clock_exceeded,
                        "sandbox.image.digest": result.image_digest,
                        "result.truncated": result.truncated,
                    },
                )
                if not result.ok:
                    # user-code failure: annotate, don't raise
                    exec_span.set_attribute("failure.terminal", True)
                    exec_span.set_attribute("sandbox.failure_reason", result.failure_reason or "")
            log.info(
                "sandbox finished",
                extra={
                    "step": "run_sandbox",
                    "exit_code": result.exit_code,
                    "wall_ms": result.wall_ms,
                    "ok": result.ok,
                    "source": "sandbox",
                },
            )
            return result

        except asyncio.CancelledError:
            await runner.kill(args.run_id, attempt)
            log.warning("sandbox cancelled; torn down", extra={"step": "run_sandbox"})
            raise
        except SandboxPolicyViolation as e:
            record_error(act_span, e, retryable=False, terminal=True)
            raise ApplicationError(str(e), type="SandboxPolicyViolation", non_retryable=True) from e
        except SandboxInfraError as e:
            record_error(act_span, e, retryable=True, terminal=False)
            raise ApplicationError(str(e), type="SandboxInfraError") from e
