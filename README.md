# temporal-agent-sandbox

A service that runs a multi-agent pipeline as a durable Temporal workflow and executes the
code it generates inside a network-isolated gVisor sandbox. It exposes a small HTTP API to
start runs and stream their status, and emits one structured trace per run.

The pipeline itself — `plan → generate_code → run_sandbox → summarize` — is deliberately
simple and deterministic. The work that matters is everything around it: durable execution
that survives crashes, isolation strong enough to run hostile code, and observability that
makes a run legible end to end.

## Key features

- Durable, resumable execution on Temporal — a worker crash or restart resumes a run from
  history with no lost progress.
- Untrusted code runs in a single-use gVisor sandbox with no network, a read-only root
  filesystem, dropped capabilities, a non-root user, and hard CPU/memory/PID/time limits.
- A minimal HTTP API: start a run, read its status, stream it over SSE, cancel it.
- One OpenTelemetry trace per run, exported to Langfuse, correlated with structured logs.
- Runs locally end to end with `docker compose`, with a documented path to a Kubernetes +
  Firecracker production deployment.

## Architecture overview

Only the API is reachable from outside. It starts workflows and reads state from Temporal,
which is the single source of truth; it never executes user code. Workers run the pipeline
and launch sandboxes — they hold no state and can be restarted freely. Untrusted code only
ever runs inside the sandbox, on the far side of the trust boundary.

```
                         TRUST BOUNDARY (host = trusted)
  CONTROL PLANE                                   DATA PLANE
   Client ──POST /runs──▶ FastAPI ──start──▶ Temporal ──schedules──▶ Worker
     ▲                      │ (stateless)        (source of truth)      │
     └──GET /runs/{id}/stream (SSE)                                      │ run_sandbox
                            │ query/tail                                 ▼
                            ▼                                  ╔══════════════════════╗
                        Temporal  ◀── history = state         ║  SANDBOX (untrusted) ║
                                                              ║  gVisor runsc        ║
   spans (OTLP) ─▶ OTel Collector ─▶ Langfuse                 ║  no NIC, ro-rootfs,   ║
   JSON logs ─────▶ stdout                                    ║  caps dropped, cgroups║
                                                              ╚══════════════════════╝
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Quick start

gVisor's `runsc` runtime needs a Linux Docker daemon, so run everything inside WSL2 Ubuntu
(or native Linux) with Docker Engine installed.

```bash
cp .env.example .env          # set API_BEARER_TOKEN
make setup-gvisor             # install and register the pinned runsc runtime (sudo)
make up                       # build the sandbox image and start the stack
```

Once up: the API is on `http://localhost:8000`, the Temporal Web UI on `:8080`, and Langfuse
on `:3000` (`admin@example.com` / `password123`). `make demo` submits a run and streams it;
`make test` runs the unit, workflow-replay, and sandbox-isolation suites.

To exercise the isolation and failure paths, append a directive to the task —
`[net]`, `[fail]`, `[hang]`, `[oom]`, `[fork]`, or `[bigout]` — and the generated code will
attempt egress, exit non-zero, loop forever, exhaust memory, fork-bomb, or flood stdout
respectively. For example, `bash scripts/demo.sh "compute [net]"` shows the run failing
because the sandbox has no network.

## API endpoints

Every endpoint requires `Authorization: Bearer <API_BEARER_TOKEN>`. Errors share one shape,
`{"error": {"code", "message", "request_id"}}`, and every response echoes `X-Request-Id`.
The run id returned by `POST /runs` is the Temporal workflow id and the trace anchor.

| Method & path            | Description                                                                                   |
| ------------------------ | --------------------------------------------------------------------------------------------- |
| `POST /runs`             | Start a run from `{task, tenant_id, params?, idempotency_key?}`. Returns the run id. Resubmitting the same request returns the existing run. |
| `GET /runs/{id}`         | Current status from a Temporal query. A failed run returns `200` with the failure in the body; an unknown id returns `404`. |
| `GET /runs/{id}/stream`  | Server-sent events: a snapshot, then a step event on each transition, then a terminal event. |
| `POST /runs/{id}/cancel` | Cancel a run; the workflow tears the sandbox down on the way out.                             |

Status is read from Temporal on every call, so the API stays stateless and these four
endpoints cover the whole lifecycle — there is no separate store to list, update, or delete.

## Isolation model

Generated code is treated as hostile on every run. Each execution is a single-use container
behind one `SandboxRunner` interface — gVisor locally, Firecracker in production. The code
is written to a file, mounted read-only, and run by a fixed entrypoint, so it is never part
of a command line and is never evaluated on the host. The container is given no network
interface, a read-only root filesystem with a small writable tmpfs, a non-root user, all
capabilities dropped, no-new-privileges, and hard CPU, memory (no swap), and PID limits. It
receives no environment and no secrets. Because there is no network device at all, there is
no egress and the cloud metadata endpoint is simply unreachable.

Two limits are enforced independently: the runner kills the container at a wall-clock
deadline, and Temporal's activity timeout is a longer backstop in case the runner itself
hangs. Output is read into a bounded buffer so a process that floods stdout cannot exhaust
the worker's memory. If the configured runtime is unavailable, the runner refuses to start
rather than fall back to a weaker one.

Local gVisor is a weaker tier than production Firecracker — its kernel runs in user space
and is itself reachable code — and the worker reaches the host Docker socket to launch
sibling sandboxes. Both are acceptable locally because the worker is trusted and never
internet-facing, and both change in production, where untrusted code runs in a Firecracker
microVM scheduled through the Kubernetes API. `tests/test_sandbox_isolation.py` asserts the
network, PID, wall-clock, filesystem, and output limits hold.

## Retry and timeout strategy

Retries are per activity, with exponential backoff, and the policy separates transient
faults from deterministic ones: a failed container launch is retried, but invalid input is
not. Workflow-level retry is disabled, because re-running a deterministic pipeline wastes
completed work and a retried workflow would be assigned a new id, breaking the run-id /
workflow-id identity.

Code that exits non-zero, runs out of memory, or exceeds its time budget is treated as a
normal result rather than an error: the sandbox activity returns that outcome and the
workflow decides the run failed. The activity only raises — and only then is it retried —
when the container could not be launched. This keeps a bad task from consuming the retry
budget and keeps platform reliability metrics distinct from user-code quality.

Timeouts are layered so each catches a different problem: the sandbox's own wall-clock limit
(30s) sits inside the activity start-to-close timeout (60s), which sits inside the workflow
run timeout (300s). A schedule-to-start timeout flags a starved or missing worker pool, and
a heartbeat on the sandbox activity turns a silent hang into a fast failure and carries
cancellation. All of these are configurable through the environment.

## Observability

Each run is a single OpenTelemetry trace: the API opens the root span, every activity is a
child span, and the sandbox execution is nested under `run_sandbox` so container setup is
distinguishable from the time the untrusted code ran. The run id threads through every span
and every log line, so traces and logs join on one key. The workflow emits no telemetry
itself — it is replayed, so a span there would be duplicated — and instead Temporal's
replay-aware interceptor propagates context into the activities, which emit the spans. Traces
go through an OTel Collector to Langfuse; the Collector strips payload attributes before
export, and payload capture is off by default.

## Production hardening

Moving to production changes the substrate, not the interfaces. The API and worker become
separate Kubernetes deployments, with workers on a dedicated, tainted node pool so untrusted
execution is physically isolated; sandboxes run as one-shot pods with a Firecracker runtime
class under restricted pod security and a default-deny network policy. Temporal runs as a
managed service to avoid operating a stateful cluster, secrets come from a secret manager
mounted only into the trusted services, and authentication moves to OIDC with tenant
identity enforced on reads. Terraform provisions the substrate while a GitOps controller
owns the workloads. The `infra/terraform` directory sketches this and is not run locally;
[ARCHITECTURE.md](ARCHITECTURE.md) covers it in more detail.

## Known limitations

- Authentication is a single static bearer token with no per-tenant ownership check; any
  valid token can read any run. Production derives tenant identity from OIDC and enforces it.
- Metrics are not emitted locally. Traces and logs ship; the metrics and SLOs described for
  production are not wired into the local Collector.
- Dependencies are version-pinned but there is no hash-locked lockfile, image digests, or
  vulnerability scan yet.
- OOM is inferred from the container exit code rather than read from an authoritative cgroup
  signal.
- Local gVisor is a weaker isolation tier than production Firecracker, and the worker mounts
  the Docker socket to launch sandboxes (replaced by the Kubernetes API in production).

## Repository structure

```
app/
  api/            FastAPI control plane: routes, auth, schemas, error handling
  temporal/       workflow, worker, client, retry/timeout policies
  activities/     side-effecting activities (agent steps + run_sandbox)
  agents/         pure pipeline logic and deterministic code generation
  sandbox/        SandboxRunner interface, gVisor/Firecracker runners, sandbox image
  observability/  OpenTelemetry tracing and structured logging
  contracts.py    typed dataclasses shared across layers
  config.py       environment-driven settings
deploy/           Dockerfile, docker-compose stack, OTel Collector, gVisor install
infra/terraform/  illustrative production infrastructure (not run locally)
tests/            unit, workflow-replay, and sandbox-isolation tests
```
