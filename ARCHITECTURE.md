# Architecture

This document describes how the service is structured and why. It assumes familiarity with
the overview in the [README](README.md) and focuses on the design: components, the path a
run takes, the Temporal workflow, the sandbox, the trust boundaries, observability, and the
production shape.

## Design goals

Two requirements drive every decision. First, execution must be **durable** — a run
survives worker crashes, deploys, and a Temporal restart, and resumes exactly where it left
off. Second, the code a run executes is **untrusted** and must be contained so that a
hostile payload cannot reach the host, the network, or another tenant.

These pull in the same direction: keep the thing that orchestrates separate from the thing
that executes. The system is therefore split into a control plane (the API and Temporal)
and a data plane (workers and sandboxes). The control plane decides what should happen and
records state; the data plane does the risky work and holds none. Because all state lives
in Temporal, data-plane processes are disposable — they can be killed or redeployed without
losing a run.

## Components

**API (FastAPI).** The only internet-facing process. It validates input, authenticates,
starts a workflow, and serves status by reading Temporal. It contains no business logic and
never executes user code, which keeps the public surface small and far from the sandbox.
It is stateless, so it scales horizontally and a restart loses nothing.

**Temporal.** The durable execution engine and the single source of truth for run state.
It persists each run's event history, schedules activities, and enforces retries and
timeouts. Using Temporal removes the need to build and operate a state machine, a queue, a
retry loop, and a status store — and to get their failure semantics right.

**Worker.** A long-running process that hosts the workflow and activity code and polls a
task queue. This is where all execution happens. Workers are stateless and interchangeable:
any worker can pick up a redelivered task, which is what makes a crash a non-event.

**Workflow.** Deterministic orchestration. It calls the pipeline's activities in order,
threads state between them, and exposes a status query. It performs no I/O so that Temporal
can replay it to reconstruct state after a failure.

**Activities.** The side-effecting units. Three are pure transforms today (`plan`,
`generate_code`, `summarize`) and would become model calls in a richer system; one,
`run_sandbox`, executes untrusted code. Activities own all non-determinism — clocks,
subprocesses, telemetry — and are individually retryable.

**Agent pipeline.** A fixed, deterministic sequence: `plan → generate_code → run_sandbox →
summarize`. It is intentionally simple. The engineering interest is in executing it durably
and safely, not in the pipeline itself.

**Sandbox runner.** The boundary between trusted and untrusted code, behind a single
`SandboxRunner` interface. The local implementation uses gVisor; the production target is
Firecracker. Selecting a runtime is a configuration change, not a rewrite.

**Observability.** One OpenTelemetry pipeline emits a trace per run to an OTel Collector,
which forwards to Langfuse. Structured logs go to stdout, correlated with traces by run id.

## Request flow

```
 client ── POST /runs ──▶ API ── start_workflow ──▶ Temporal
   ▲                       │                            │ schedules
   │  GET /runs/{id}       │ query / tail               ▼
   │  GET /runs/{id}/stream│                          Worker
   └───────────────────────┘                  plan → generate_code
                                              → run_sandbox → summarize
                                                           │
                                                           ▼
                                                  gVisor sandbox (untrusted)
```

`POST /runs` derives a workflow id from the tenant and an idempotency key (or a hash of the
request), starts the workflow, and returns immediately. The id is the run id; the same
request started twice resolves to the same run rather than a duplicate.

Status is read, never stored by the API. `GET /runs/{id}` answers from a Temporal query of
the workflow's in-memory state, or from the workflow result once the run is closed. The SSE
endpoint polls that same query on a short interval and emits an event whenever the state
advances, ending with a terminal event. Because the API holds no stream state, a client can
reconnect at any time and re-read the current snapshot.

## Temporal workflow

The workflow is a small state machine: set the current stage, run an activity, record the
result, repeat. Its correctness depends on **determinism**. Temporal reconstructs a running
workflow by replaying its history through the workflow function, so that function must
produce the same sequence of commands every time. Anything non-deterministic — a clock, a
random value, network or disk I/O — would diverge on replay. The workflow therefore does
none of these; every side effect is delegated to an activity. Shared data types are plain
standard-library dataclasses imported into the workflow sandbox explicitly, and values like
"did the sandbox succeed" are computed properties rather than stored fields, so they never
participate in serialization and are recomputed after replay.

Resilience lives at the activity level. Each activity has a retry policy with exponential
backoff, and the policy distinguishes transient faults (retry) from deterministic ones
(fail fast). Workflow-level retry is deliberately disabled: it would re-run already
completed steps and, because a retried workflow gets a new run id, it would break the
invariant that the run id equals the workflow id.

One decision is worth stating plainly because it shapes the error model. A piece of
untrusted code that exits non-zero, runs out of memory, or exceeds its time budget is a
**normal outcome**, not a platform failure. The `run_sandbox` activity therefore succeeds
and returns a result describing what happened; the workflow inspects that result and decides
whether the run completed or failed. The activity only raises — and only then does Temporal
retry — when the sandbox could not be launched at all. This keeps platform reliability
metrics separate from user-code quality and avoids wasting retries on code that will fail
the same way every time.

Timeouts are layered so each guards a distinct failure. The sandbox enforces a wall-clock
limit itself; the activity's start-to-close timeout is a longer backstop in case the runner
hangs; the workflow's run timeout bounds the whole run. A schedule-to-start timeout
surfaces a starved or absent worker pool without being so tight that it trips during a
normal cold start. A heartbeat on the long sandbox activity turns a silent hang into a fast,
attributable failure and is the channel through which cancellation is delivered.

## Sandbox

Each execution is a single-use container with no persistent state. The runner writes the
generated code to a file, mounts it read-only, and runs it through a fixed entrypoint — the
code is data, never part of the command line, and is never evaluated on the host. The
container has no network interface, a read-only root filesystem with a small writable tmpfs
for scratch, a non-root user, all capabilities dropped, no-new-privileges, and hard CPU,
memory (no swap), and PID limits. It receives no environment and no secrets, so there is
nothing in it worth stealing. Output is read into a bounded tail buffer rather than
collected whole, so a process that floods stdout cannot pressure the worker's memory.

The runner enforces the wall-clock limit directly and tears the container down on timeout or
cancellation; teardown is idempotent, so it is safe whether the run succeeded, timed out, or
was cancelled. If the configured runtime is not actually available, the runner refuses to
start rather than silently falling back to a weaker one.

gVisor is the local runtime because it provides a real isolation boundary — a user-space
kernel that intercepts the workload's syscalls — without requiring hardware virtualization,
so it runs on a developer machine. It is honestly a weaker tier than the production target:
its kernel is itself reachable code. Production uses Firecracker microVMs, which place a
hardware-virtualized boundary around untrusted code. Both sit behind the same interface, so
the difference is configuration.

Locally the worker launches sibling sandbox containers through the host Docker socket, with
code staged in a directory shared at an identical path on host and worker so the bind mount
resolves on the host daemon. Mounting the Docker socket grants the worker broad host
control; this is acceptable only because the worker is trusted and never internet-facing,
and it disappears in production, where the worker submits a one-shot pod through the
Kubernetes API instead.

## Trust boundaries

The boundary that matters is between the `run_sandbox` activity (trusted host code) and the
sandboxed process (untrusted). Everything in the API, Temporal, the workflow, and the
activities is trusted; the moment code crosses into the sandbox it loses network access,
secrets, host filesystem access, and unbounded time and memory. The activity is the airlock:
code goes in as a file, a structured result comes out.

Around that core boundary are several others. Only the API faces the internet; workers and
sandboxes are never exposed. The API authenticates callers before doing any work. Telemetry
leaving for the trace backend is a data-egress boundary, which is why payload capture is off
by default and the Collector strips payload attributes before export. In production each of
these is reinforced — a network policy that denies by default, a microVM instead of a
shared-kernel sandbox, secrets delivered only to the trusted services — so no single control
is load-bearing on its own.

## Observability

A run is one trace. The API opens the root span; each activity is a child span, and the
sandbox execution is a nested span beneath `run_sandbox`, so the time spent setting up the
container is distinguishable from the time the untrusted code actually ran. Spans carry the
run and tenant ids, the step name, and outcome attributes such as exit code and duration;
they do not carry raw code or output unless payload capture is explicitly enabled.

Correlation has to cross an asynchronous, replayed boundary. The workflow itself emits no
telemetry — it runs repeatedly during replay, so a span emitted there would be duplicated or
inconsistent. Instead, Temporal's replay-aware tracing interceptor propagates context from
the API into the workflow and on into activities, and the activities emit the business spans.
The run id is the spine: generated at submission, used as the workflow id, and stamped onto
every span and log line, so trace search and log search join on one key.

The Collector decouples the application from the backend. It batches, applies redaction, and
forwards to Langfuse; pointing traces at a different backend is a configuration change.

## Production architecture

The local stack proves the design; production changes the substrate beneath it without
changing the interfaces. The API and worker run as separate Kubernetes deployments. The API
sits behind a load balancer; workers run on a dedicated, tainted node pool so that untrusted
execution is physically isolated from everything else. Sandboxes run as one-shot pods with a
Firecracker runtime class, under a restricted pod-security standard and a default-deny
network policy — the no-network guarantee re-expressed as cluster policy.

Temporal is run as a managed service (Temporal Cloud) rather than self-hosted, which removes
the largest operational burden in the system: operating a sharded, stateful cluster and its
datastore. Secrets come from a secret manager mounted into the trusted services only;
authentication moves from a static token to OIDC, with tenant identity derived from verified
claims and enforced on reads. Provisioning splits along ownership lines: Terraform owns the
immutable substrate (network, cluster, secrets, registries), and a GitOps controller owns
in-cluster workloads, so application rollout does not run through slow infrastructure
applies. The `infra/terraform` directory sketches this; it is illustrative and not part of
the local stack.
