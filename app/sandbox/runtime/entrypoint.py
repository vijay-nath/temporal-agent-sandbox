#!/usr/bin/env python3
"""Fixed sandbox entrypoint: execute the read-only /sandbox/code.py via runpy, so untrusted
code is never placed on the command line or eval'd. The host-side runner captures
stdout/stderr and the exit code."""

import runpy
import sys
import traceback

CODE_PATH = "/sandbox/code.py"

if __name__ == "__main__":
    try:
        runpy.run_path(CODE_PATH, run_name="__main__")
    except SystemExit:
        raise  # honor the untrusted program's own exit code
    except BaseException:
        traceback.print_exc()
        sys.exit(1)
