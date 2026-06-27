"""Pure pipeline transforms (no I/O), kept separate from the Temporal activities so they are
unit-testable and the workflow stays deterministic."""

from __future__ import annotations

from app.agents.codegen import DIRECTIVES, generate_program
from app.contracts import Plan, SummarizeArgs


def parse_directive(task: str) -> str:
    """Return the directive named in the task (``[fail]``, ``directive:oom``, …), or 'normal'."""
    low = task.lower()
    for d in DIRECTIVES:
        if d == "normal":
            continue
        if f"[{d}]" in low or f"directive:{d}" in low:
            return d
    return "normal"


def plan(task: str) -> Plan:
    directive = parse_directive(task)
    steps = [
        "analyze task",
        f"emit '{directive}' program",
        "execute in sandbox",
        "summarize result",
    ]
    return Plan(task=task, directive=directive, steps=steps)


def generate_code(plan_: Plan) -> str:
    return generate_program(plan_.directive, plan_.task)


def summarize(args: SummarizeArgs) -> str:
    s = args.sandbox
    head = s.stdout_tail.strip().replace("\n", " ")[:500]
    if s.ok:
        return (
            f"Run completed (directive={args.plan.directive}, exit=0, {s.wall_ms}ms via "
            f"{s.runtime}). Output: {head}"
        )
    err = s.stderr_tail.strip().replace("\n", " ")[:500]
    return (
        f"Run failed (directive={args.plan.directive}, reason={s.failure_reason}, "
        f"exit={s.exit_code}, {s.wall_ms}ms via {s.runtime}). Stderr: {err}"
    )
