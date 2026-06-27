"""Retry policies and timeouts (env-driven). Resolved once at process start so the values are
stable across workflow replays."""

from __future__ import annotations

from datetime import timedelta

from temporalio.common import RetryPolicy

from app.config import get_settings

_s = get_settings()

# User-code failures never raise (they're returned in SandboxResult), so the non-retryable
# lists below cover only true do-not-retry conditions.
AGENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=_s.max_attempts,
)

SANDBOX_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=_s.max_attempts,
    non_retryable_error_types=["SandboxPolicyViolation", "PayloadTooLarge"],
)

AGENT_START_TO_CLOSE = timedelta(seconds=_s.agent_start_to_close_s)
SANDBOX_START_TO_CLOSE = timedelta(seconds=_s.sandbox_start_to_close_s)
SANDBOX_HEARTBEAT = timedelta(seconds=_s.sandbox_heartbeat_s)
SCHEDULE_TO_START = timedelta(seconds=_s.schedule_to_start_s)
WORKFLOW_RUN_TIMEOUT = timedelta(seconds=_s.workflow_run_timeout_s)
