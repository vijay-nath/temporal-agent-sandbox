#!/usr/bin/env bash
# Install a pinned gVisor (runsc) release and register it with the Docker daemon.
# Run inside WSL2 Ubuntu via `make setup-gvisor` (sudo); pinned by version + checksum.
set -euo pipefail

RUNSC_VERSION="${RUNSC_VERSION:-20241216.0}"
ARCH="$(uname -m)"
BASE="https://storage.googleapis.com/gvisor/releases/release/${RUNSC_VERSION}/${ARCH}"

echo ">> Installing gVisor runsc ${RUNSC_VERSION} for ${ARCH}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cd "$TMP"

for f in runsc containerd-shim-runsc-v1; do
  wget -q "${BASE}/${f}" "${BASE}/${f}.sha512"
  sha512sum -c "${f}.sha512"
  chmod +x "${f}"
done

sudo mv runsc containerd-shim-runsc-v1 /usr/local/bin/

# Registers the `runsc` runtime in /etc/docker/daemon.json (idempotent).
sudo /usr/local/bin/runsc install

echo ">> Restarting Docker"
sudo systemctl restart docker 2>/dev/null || sudo service docker restart

echo ">> Done. Verify with: docker info | grep -iA2 runtimes"
