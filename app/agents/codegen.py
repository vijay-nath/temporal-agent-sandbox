"""Deterministic stand-in for an LLM code-generation step.

Given a directive parsed from the task, returns a small Python program for the sandbox to
run. Each directive exercises a specific sandbox behavior (network denial, OOM, PID cap,
wall-clock, output cap, non-zero exit).
"""

from __future__ import annotations

import json

DIRECTIVES = ("normal", "fail", "hang", "oom", "net", "fork", "bigout")

_NORMAL = """\
import json, hashlib
TASK = {task}
result = {{
    "task_len": len(TASK),
    "word_count": len(TASK.split()),
    "sha256": hashlib.sha256(TASK.encode()).hexdigest(),
}}
print(json.dumps(result))
"""

_FAIL = """\
import sys
print("intentional failure (directive=fail)")
sys.exit(3)
"""

_HANG = """\
# Spin forever; the sandbox wall-clock must kill this.
while True:
    pass
"""

_OOM = """\
# Grow memory until the cgroup OOM-killer fires.
buf = bytearray()
chunk = bytes(10 * 1024 * 1024)
while True:
    buf += chunk
"""

_NET = """\
# Attempt egress; the sandbox has NO network interface, so this must fail.
# Fail CLOSED: if the connection ever succeeds, exit non-zero so a broken isolation
# surfaces as a FAILED run (not an ok run), not merely a printed warning.
import socket, sys
try:
    s = socket.create_connection(("1.1.1.1", 80), timeout=5)
    print("NETWORK REACHABLE - ISOLATION FAILED")
    s.close()
    sys.exit(99)
except OSError as e:
    print("network blocked:", e)
    sys.exit(42)
"""

_FORK = """\
# Try to exceed the PID cap; the limit must contain this.
import os, sys, time
children = []
try:
    for _ in range(1000):
        pid = os.fork()
        if pid == 0:
            time.sleep(60)
            os._exit(0)
        children.append(pid)
except OSError as e:
    print("pid limit hit:", e)
    sys.exit(7)
print("forked", len(children))
sys.exit(0)
"""

_BIGOUT = """\
# Emit more than the output cap; the runner must truncate.
import sys
line = "A" * 1024
for _ in range(5000):
    sys.stdout.write(line)
sys.stdout.write("\\n")
"""

_TEMPLATES = {
    "fail": _FAIL,
    "hang": _HANG,
    "oom": _OOM,
    "net": _NET,
    "fork": _FORK,
    "bigout": _BIGOUT,
}


def generate_program(directive: str, task: str) -> str:
    """Return the (untrusted) Python source for a directive. Deterministic."""
    if directive == "normal":
        return _NORMAL.format(task=json.dumps(task))
    return _TEMPLATES[directive]
