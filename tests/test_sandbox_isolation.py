"""The security proof: assert the sandbox actually denies network/resources.

Integration test — requires Docker + a registered runsc runtime + the sandbox image:
    make setup-gvisor && make sandbox-image
    pytest -m integration

Run it under the runtime you ship (SANDBOX_RUNTIME=runsc). These are the concrete
assertions behind "no host network access from the sandbox".
"""

from __future__ import annotations

import pytest

from app.agents.codegen import generate_program
from app.config import get_settings
from app.contracts import ResourceLimits, RunSandboxArgs
from app.sandbox.gvisor import GvisorRunner

pytestmark = pytest.mark.integration


@pytest.fixture
def runner() -> GvisorRunner:
    return GvisorRunner(get_settings().sandbox_runtime)


async def _run(runner: GvisorRunner, code: str, limits: ResourceLimits | None = None):
    args = RunSandboxArgs(
        run_id="test-iso",
        tenant_id="test",
        code=code,
        limits=limits or ResourceLimits(),
        runtime=runner.runtime,
    )
    return await runner.run(args, attempt=1)


async def test_runtime_is_available(runner):
    # Fail-closed preflight must pass (runsc registered) before the rest are meaningful.
    await runner.preflight()


async def test_normal_program_succeeds(runner):
    res = await _run(runner, generate_program("normal", "hello world"))
    assert res.ok and res.exit_code == 0
    assert "sha256" in res.stdout_tail


async def test_network_is_denied(runner):
    res = await _run(runner, generate_program("net", ""))
    assert not res.ok
    assert res.exit_code == 42 or "blocked" in (res.stdout_tail + res.stderr_tail).lower()


async def test_pid_cap_contains_fork(runner):
    res = await _run(runner, generate_program("fork", ""))
    assert res.exit_code == 7  # os.fork() failed at the PID limit


async def test_wall_clock_kills_infinite_loop(runner):
    res = await _run(runner, generate_program("hang", ""), ResourceLimits(wall_clock_s=3))
    assert res.wall_clock_exceeded


async def test_readonly_rootfs(runner):
    res = await _run(runner, "open('/etc/should_not_write', 'w').write('x')")
    assert not res.ok  # read-only filesystem


async def test_output_is_truncated(runner):
    res = await _run(runner, generate_program("bigout", ""), ResourceLimits(output_max_bytes=1024))
    assert res.truncated
