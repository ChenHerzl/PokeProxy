#!/usr/bin/env bash
# Explicit opt-in: download two pinned CLIs to this checkout; no sudo/packages.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
for command in curl sha256sum install mktemp uname; do
  command -v "$command" >/dev/null || { echo "Missing prerequisite: $command" >&2; exit 1; }
done
[[ "$(uname -s)" == Linux ]] || { echo 'This installer supports Linux only.' >&2; exit 1; }
case "$(uname -m)" in
  x86_64) ARCH=amd64 ;;
  aarch64) ARCH=arm64 ;;
  *) echo 'Supported architectures: x86_64 and aarch64.' >&2; exit 1 ;;
esac
mkdir -p "$ROOT/.local/bin"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
cd "$TEMP"
echo 'Downloading kind v0.33.0 and kubectl v1.36.2 into .local/bin (no system installation).'
curl -fsSL --retry 2 --connect-timeout 10 --max-time 120 \
  "https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-$ARCH" -o "kind-linux-$ARCH"
curl -fsSL --retry 2 --connect-timeout 10 --max-time 120 \
  "https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-$ARCH.sha256sum" -o kind.sha256sum
sha256sum --check kind.sha256sum
curl -fsSL --retry 2 --connect-timeout 10 --max-time 120 \
  "https://dl.k8s.io/release/v1.36.2/bin/linux/$ARCH/kubectl" -o kubectl
curl -fsSL --retry 2 --connect-timeout 10 --max-time 120 \
  "https://dl.k8s.io/release/v1.36.2/bin/linux/$ARCH/kubectl.sha256" -o kubectl.sha256
echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check
install -m 755 "kind-linux-$ARCH" "$ROOT/.local/bin/kind"
install -m 755 kubectl "$ROOT/.local/bin/kubectl"
echo 'Tools ready. Run make up; the Make targets use .local/bin automatically.'
