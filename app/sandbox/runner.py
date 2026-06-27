"""Sandbox execution interface; the runtime is selected by config (runsc local, firecracker prod).

The error taxonomy drives the retry policy: SandboxInfraError is retryable, SandboxPolicyViolation
is not. Ordinary user-code failure is not an error — it is returned in SandboxResult and the
workflow decides the terminal status.
"""

from __future__ import annotations

import abc
from collections.abc import Callable

from app.contracts import RunSandboxArgs, SandboxResult

HeartbeatFn = Callable[[dict], None]


class SandboxInfraError(RuntimeError):
    """Transient fault (runtime/launch failure); retryable."""


class SandboxPolicyViolation(RuntimeError):
    """Non-retryable policy condition, e.g. the requested runtime is unavailable (fail closed)."""


class SandboxRunner(abc.ABC):
    @abc.abstractmethod
    async def run(
        self, args: RunSandboxArgs, *, attempt: int = 1, heartbeat: HeartbeatFn | None = None
    ) -> SandboxResult:
        """Execute the untrusted code once and return the outcome."""

    @abc.abstractmethod
    async def kill(self, run_id: str, attempt: int = 1) -> None:
        """Idempotently destroy the sandbox for (run_id, attempt)."""

    async def preflight(self) -> None:
        """Optional fail-closed startup check."""
        return None


def get_runner(runtime: str) -> SandboxRunner:
    if runtime in ("runsc", "runc"):
        from app.sandbox.gvisor import GvisorRunner

        return GvisorRunner(runtime)
    if runtime == "firecracker":
        from app.sandbox.firecracker import FirecrackerRunner

        return FirecrackerRunner()
    raise SandboxPolicyViolation(f"unknown sandbox runtime: {runtime!r}")
