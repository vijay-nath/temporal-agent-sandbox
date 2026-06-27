# Minimal image for running untrusted code: interpreter + stdlib only, no shell or network
# tooling. Runs non-root with a read-only rootfs; the entrypoint executes a mounted code file.
FROM python:3.12-slim

# Don't write .pyc (rootfs is read-only) and flush stdout immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY entrypoint.py /sandbox/entrypoint.py

# 65534 (nobody) exists in the base image; --user is also set at runtime.
USER 65534:65534
WORKDIR /work
ENTRYPOINT ["python", "/sandbox/entrypoint.py"]
