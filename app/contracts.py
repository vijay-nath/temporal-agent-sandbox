"""Typed dataclasses shared across the API, workflow, activities and sandbox.

Standard-library only so they import cleanly inside the Temporal workflow sandbox; derived
values are properties so they stay out of serialization and survive replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- run inputs / limits ----------------------------------------------------


@dataclass
class ResourceLimits:
    """Resource caps applied to each sandbox execution."""

    cpus: float = 1.0
    memory_mb: int = 256
    pids: int = 128
    wall_clock_s: int = 30
    output_max_bytes: int = 1_048_576
    tmpfs_mb: int = 64
    nofile: int = 256


@dataclass
class RunInput:
    """Workflow input. run_id is also the Temporal workflow id."""

    run_id: str
    tenant_id: str
    task: str
    limits: ResourceLimits
    sandbox_runtime: str


# --- pipeline artifacts -----------------------------------------------------


@dataclass
class Plan:
    task: str
    directive: str  # parsed from the task: normal|fail|hang|oom|net|fork|bigout
    steps: list[str]


@dataclass
class RunSandboxArgs:
    """Input to the run_sandbox activity."""

    run_id: str
    tenant_id: str
    code: str
    limits: ResourceLimits
    runtime: str


@dataclass
class SandboxResult:
    """A sandbox outcome. The activity returns this even on user-code failure; the workflow
    inspects it to decide the run's terminal status."""

    runtime: str
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    wall_ms: int
    oom_killed: bool = False
    wall_clock_exceeded: bool = False  # in-runner wall-clock kill
    image_digest: str | None = None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.oom_killed and not self.wall_clock_exceeded

    @property
    def failure_reason(self) -> str | None:
        if self.ok:
            return None
        if self.wall_clock_exceeded:
            return "wall_clock_exceeded"
        if self.oom_killed:
            return "oom_killed"
        return f"nonzero_exit:{self.exit_code}"


@dataclass
class SummarizeArgs:
    plan: Plan
    sandbox: SandboxResult


# --- status / result --------------------------------------------------------


@dataclass
class StepStatus:
    name: str
    state: str  # "running" | "completed" | "failed"
    detail: str | None = None


@dataclass
class RunStatus:
    """Returned by the workflow's status query."""

    run_id: str
    stage: str  # queued|plan|generate_code|run_sandbox|summarize|completed|failed
    steps: list[StepStatus] = field(default_factory=list)


@dataclass
class RunResult:
    """Terminal workflow result."""

    run_id: str
    status: str  # "completed" | "failed"
    summary: str | None = None
    code: str | None = None
    sandbox: SandboxResult | None = None
    failure_reason: str | None = None

    @classmethod
    def completed(cls, run_id: str, summary: str, code: str, sandbox: SandboxResult) -> RunResult:
        return cls(run_id=run_id, status="completed", summary=summary, code=code, sandbox=sandbox)

    @classmethod
    def failed(
        cls,
        run_id: str,
        reason: str,
        sandbox: SandboxResult | None = None,
        code: str | None = None,
    ) -> RunResult:
        return cls(run_id=run_id, status="failed", code=code, sandbox=sandbox, failure_reason=reason)
