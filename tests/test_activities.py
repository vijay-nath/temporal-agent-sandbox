"""Unit tests for the pure pipeline logic and contract semantics (no Temporal, no Docker)."""

from __future__ import annotations

import pytest

from app.agents import pipeline
from app.agents.codegen import DIRECTIVES, generate_program
from app.contracts import SandboxResult, SummarizeArgs


@pytest.mark.parametrize(
    "task,expected",
    [
        ("just summarize this", "normal"),
        ("please [fail] now", "fail"),
        ("directive:oom", "oom"),
        ("try [net] egress", "net"),
        ("[fork] bomb", "fork"),
    ],
)
def test_parse_directive(task, expected):
    assert pipeline.parse_directive(task) == expected


def test_plan_shape():
    p = pipeline.plan("compute [fail]")
    assert p.directive == "fail"
    assert p.task == "compute [fail]"
    assert len(p.steps) == 4


def test_generated_code_is_valid_python_for_every_directive():
    for d in DIRECTIVES:
        code = generate_program(d, "hello world")
        compile(code, f"<{d}>", "exec")  # must parse


def test_normal_codegen_embeds_task_as_literal():
    code = generate_program("normal", 'tricky") + __import__("os")')
    # Task is embedded via json.dumps, so it cannot break out of the string literal.
    compile(code, "<normal>", "exec")
    assert "hashlib" in code


def test_sandbox_result_ok():
    r = SandboxResult(runtime="runsc", exit_code=0, stdout_tail="{}", stderr_tail="", wall_ms=5)
    assert r.ok is True
    assert r.failure_reason is None


def test_sandbox_result_failure_classification():
    nonzero = SandboxResult(runtime="runsc", exit_code=3, stdout_tail="", stderr_tail="x", wall_ms=5)
    assert not nonzero.ok and nonzero.failure_reason == "nonzero_exit:3"

    oom = SandboxResult(
        runtime="runsc", exit_code=137, stdout_tail="", stderr_tail="", wall_ms=5, oom_killed=True
    )
    assert oom.failure_reason == "oom_killed"

    wc = SandboxResult(
        runtime="runsc", exit_code=124, stdout_tail="", stderr_tail="", wall_ms=30000,
        wall_clock_exceeded=True,
    )
    assert wc.failure_reason == "wall_clock_exceeded"


def test_summarize_success_vs_failure():
    p = pipeline.plan("hello")
    ok = SandboxResult(runtime="runsc", exit_code=0, stdout_tail='{"x":1}', stderr_tail="", wall_ms=12)
    assert "completed" in pipeline.summarize(SummarizeArgs(plan=p, sandbox=ok))

    bad = SandboxResult(runtime="runsc", exit_code=3, stdout_tail="", stderr_tail="boom", wall_ms=12)
    assert "failed" in pipeline.summarize(SummarizeArgs(plan=p, sandbox=bad))
