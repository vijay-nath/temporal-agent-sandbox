"""Production sandbox runtime: a one-shot Kubernetes Pod with runtimeClassName=firecracker.

Not wired for local dev (no reliable KVM under WSL2); use runsc locally.
"""

from __future__ import annotations

from app.contracts import RunSandboxArgs, SandboxResult
from app.sandbox.runner import HeartbeatFn, SandboxPolicyViolation, SandboxRunner


class FirecrackerRunner(SandboxRunner):
    async def preflight(self) -> None:
        # Fail closed at worker startup rather than burning retries at run time.
        raise SandboxPolicyViolation(
            "firecracker runtime is the production target (Kata-Firecracker RuntimeClass on "
            "KVM nodes) and is not available in this build; set SANDBOX_RUNTIME=runsc."
        )

    async def run(
        self, args: RunSandboxArgs, *, attempt: int = 1, heartbeat: HeartbeatFn | None = None
    ) -> SandboxResult:
        raise NotImplementedError(
            "FirecrackerRunner is the production target (Kata-Firecracker RuntimeClass on "
            "KVM nodes) and is not implemented for local dev. Use SANDBOX_RUNTIME=runsc."
        )

    async def kill(self, run_id: str, attempt: int = 1) -> None:
        return None
