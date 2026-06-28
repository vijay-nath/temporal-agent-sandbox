"""gVisor (runsc) sandbox runner.

Runs untrusted code in a single-shot container with no network, a read-only rootfs, dropped
capabilities, a non-root user, and hard cgroup/wall-clock limits. The code is mounted
read-only and run by the image entrypoint. The runtime is passed explicitly so Docker fails
closed if it is not registered, and output is read into a bounded buffer so a flood cannot
exhaust worker memory. The same class drives runc as a weaker fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from pathlib import Path

from app.config import get_settings
from app.contracts import ResourceLimits, RunSandboxArgs, SandboxResult
from app.sandbox.runner import (
    HeartbeatFn,
    SandboxInfraError,
    SandboxPolicyViolation,
    SandboxRunner,
)

log = logging.getLogger(__name__)

# Docker uses 125/126/127 for launch failures, but user code can also exit with them, so we
# treat them as infra only when stderr shows a launch signature (see _classify_infra).
_LAUNCH_RCS = (125, 126, 127)
_LAUNCH_SIGNATURES = (
    "docker:",
    "error response from daemon",
    "oci runtime",
    "executable file not found",
    "unknown runtime",
    "no such image",
    "manifest unknown",
)
_READ_CHUNK = 65536


async def _run_cmd(cmd: list[str], timeout: float = 15.0) -> tuple[int, bytes, bytes]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        # docker CLI absent: treat as a non-zero result so preflight fails closed rather
        # than letting FileNotFoundError escape (which would crash-loop / mis-classify retries).
        return 127, b"", b"docker CLI not found on PATH"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return 124, b"", b"control command timed out"
    return proc.returncode or 0, out, err


async def _read_capped(stream: asyncio.StreamReader | None, limit: int) -> tuple[bytes, bool]:
    """Drain a pipe keeping only the last ``limit`` bytes (the tail). Bounded memory."""
    buf = bytearray()
    total = 0
    if stream is None:
        return b"", False
    while True:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        buf += chunk
        if len(buf) > limit:
            del buf[: len(buf) - limit]  # keep only the trailing `limit` bytes
    return bytes(buf), total > limit


class GvisorRunner(SandboxRunner):
    def __init__(self, runtime: str) -> None:
        self.runtime = runtime
        self._settings = get_settings()
        self.image = self._settings.sandbox_image

    def _container_name(self, run_id: str, attempt: int) -> str:
        # hash so distinct run_ids can't collide on a truncated name
        digest = hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:16]  # noqa: S324
        return f"sbx-{digest}-{attempt}"

    async def preflight(self) -> None:
        if self.runtime == "runc":
            return  # runc is the default runtime and always present
        rc, out, _ = await _run_cmd(["docker", "info", "--format", "{{json .Runtimes}}"])
        if rc != 0 or f'"{self.runtime}"' not in out.decode("utf-8", "replace"):
            raise SandboxPolicyViolation(
                f"sandbox runtime {self.runtime!r} is not registered with Docker; refusing "
                f"to fall back to runc (fail-closed). Run `make setup-gvisor`."
            )

    def _docker_run_cmd(
        self, name: str, code_file: Path, input_file: Path, limits: ResourceLimits
    ) -> list[str]:
        cmd = [
            "docker", "run", "--rm",
            "--name", name,
            "--runtime", self.runtime,
            "--network", "none",                       # no NIC: no egress, no metadata
            "--read-only",
            "--user", "65534:65534",                    # nobody
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--cpus", str(limits.cpus),
            "--memory", f"{limits.memory_mb}m",
            "--memory-swap", f"{limits.memory_mb}m",    # == memory => no swap, OOM-kill
            "--pids-limit", str(limits.pids),
            "--ulimit", f"nofile={limits.nofile}:{limits.nofile}",
            "--tmpfs", f"/work:rw,noexec,nosuid,size={limits.tmpfs_mb}m,nr_inodes=4096",
            "--workdir", "/work",
            "-v", f"{code_file}:/sandbox/code.py:ro",   # code mounted read-only
            "-v", f"{input_file}:/sandbox/input.json:ro",
        ]
        if self._settings.sandbox_seccomp_path:
            cmd += ["--security-opt", f"seccomp={self._settings.sandbox_seccomp_path}"]
        cmd += [self.image]  # entrypoint runs the mounted code; code is never in argv
        return cmd

    async def _image_digest(self) -> str | None:
        rc, out, _ = await _run_cmd(
            ["docker", "image", "inspect", "--format", "{{.Id}}", self.image]
        )
        digest = out.decode("utf-8", "replace").strip()
        return digest if rc == 0 and digest else None

    async def run(
        self, args: RunSandboxArgs, *, attempt: int = 1, heartbeat: HeartbeatFn | None = None
    ) -> SandboxResult:
        limits = args.limits
        name = self._container_name(args.run_id, attempt)
        digest = await self._image_digest()
        loop = asyncio.get_running_loop()

        share_dir = self._settings.sandbox_share_dir or None
        with tempfile.TemporaryDirectory(prefix="sbx-", dir=share_dir) as tmp:
            code_file = Path(tmp) / "code.py"
            input_file = Path(tmp) / "input.json"
            code_file.write_text(args.code, encoding="utf-8")
            input_file.write_text("{}", encoding="utf-8")
            # readable/traversable by the non-root sandbox UID through the read-only mount
            os.chmod(tmp, 0o755)
            os.chmod(code_file, 0o644)
            os.chmod(input_file, 0o644)
            cmd = self._docker_run_cmd(name, code_file, input_file, limits)

            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            cap = limits.output_max_bytes
            out_task = asyncio.ensure_future(_read_capped(proc.stdout, cap))
            err_task = asyncio.ensure_future(_read_capped(proc.stderr, cap))
            wait_task = asyncio.ensure_future(proc.wait())
            start = loop.time()

            try:
                while True:
                    elapsed = loop.time() - start
                    remaining = limits.wall_clock_s - elapsed
                    if remaining <= 0:
                        # in-runner wall-clock kill; Temporal's start_to_close is the backstop
                        await self.kill(args.run_id, attempt)
                        wait_task.cancel()
                        out, so_t = await self._safe(out_task)
                        err, se_t = await self._safe(err_task)
                        return SandboxResult(
                            runtime=self.runtime, exit_code=124,
                            stdout_tail=out.decode("utf-8", "replace"),
                            stderr_tail=err.decode("utf-8", "replace"),
                            wall_ms=int(elapsed * 1000), wall_clock_exceeded=True,
                            truncated=so_t or se_t, image_digest=digest,
                        )
                    wait_for = min(self._settings.sandbox_heartbeat_s, max(remaining, 0.1))
                    done, _pending = await asyncio.wait({wait_task}, timeout=wait_for)
                    if wait_task in done:
                        break
                    if heartbeat:
                        heartbeat(
                            {"phase": "running", "elapsed_ms": int((loop.time() - start) * 1000)}
                        )
            except asyncio.CancelledError:
                # tear down the sandbox on cancellation, then propagate
                await self.kill(args.run_id, attempt)
                for t in (out_task, err_task, wait_task):
                    t.cancel()
                raise

            rc = wait_task.result()
            wall_ms = int((loop.time() - start) * 1000)
            out, so_t = await self._safe(out_task)
            err, se_t = await self._safe(err_task)
            err_text = err.decode("utf-8", "replace")
            self._classify_infra(rc, err_text)  # raises for genuine launch failures only

            return SandboxResult(
                runtime=self.runtime,
                exit_code=rc,
                stdout_tail=out.decode("utf-8", "replace"),
                stderr_tail=err_text,
                wall_ms=wall_ms,
                oom_killed=(rc == 137),  # SIGKILL: typically the cgroup OOM-killer
                image_digest=digest,
                truncated=so_t or se_t,
            )

    def _classify_infra(self, rc: int, err_text: str) -> None:
        """Raise for genuine container-launch failures; user-code exits pass through."""
        if rc not in _LAUNCH_RCS:
            return
        lowered = err_text.lower()
        if not any(sig in lowered for sig in _LAUNCH_SIGNATURES):
            return  # untrusted code legitimately exited 125/126/127 — a normal result
        if ("unknown runtime" in lowered) or (self.runtime in lowered):
            raise SandboxPolicyViolation(f"runtime unavailable: {err_text.strip()}")
        raise SandboxInfraError(f"sandbox failed to start (docker rc={rc}): {err_text.strip()}")

    @staticmethod
    async def _safe(task: asyncio.Future) -> tuple[bytes, bool]:
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except Exception:  # noqa: BLE001 — best-effort output collection
            task.cancel()
            return b"", False

    async def kill(self, run_id: str, attempt: int = 1) -> None:
        name = self._container_name(run_id, attempt)
        await _run_cmd(["docker", "rm", "-f", name], timeout=10)
