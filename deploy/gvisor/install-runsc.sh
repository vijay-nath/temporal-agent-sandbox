#!/usr/bin/env bash
# Install a pinned gVisor (runsc) release and register it with the Docker daemon, then reload
# the daemon. Native Docker Engine is restarted automatically; Docker Desktop (WSL2) manages its
# own daemon, so the script prints manual restart steps instead. Run via `make setup-gvisor` (sudo).
set -euo pipefail

RUNSC_VERSION="${RUNSC_VERSION:-20260622.0}"
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

echo ">> Reloading the Docker daemon to pick up the runsc runtime"

manual_restart_notice() {
  cat <<'EOF'
>> Could not restart the Docker daemon automatically.
   This is expected on Docker Desktop (WSL2): it runs its own daemon in a separate VM and
   cannot be restarted from here. Restart Docker Desktop manually (tray/menu -> Restart), then:

       docker info        # 'runsc' must appear under "Runtimes"

   If runsc does not appear, Docker Desktop is not using this distro's runtime; the supported
   sandbox path is a native Docker Engine inside WSL2. The worker stays fail-closed until runsc
   is registered.
EOF
}

DOCKER_OS="$(docker info --format '{{.OperatingSystem}}' 2>/dev/null || true)"

if printf '%s' "$DOCKER_OS" | grep -qi "docker desktop"; then
  echo ">> Docker Desktop (WSL2) detected; not attempting an automatic daemon restart."
  manual_restart_notice
  exit 0
fi

# Native Docker Engine: restart the daemon (systemd, else SysV). If neither is manageable
# (e.g. an undetected Docker Desktop), fall back to manual instructions — without masking the
# install, which already succeeded above.
if sudo systemctl restart docker 2>/dev/null || sudo service docker restart 2>/dev/null; then
  echo ">> Done. Verify with: docker info | grep -iA2 runtimes"
else
  manual_restart_notice
  exit 0
fi
