#!/usr/bin/env bash
# Linux amd64, used by GitHub-hosted runners and local workflow-equivalent checks.
set -euo pipefail
DEST="${1:?usage: install-ci-tools.sh ABSOLUTE_DESTINATION}"
[[ "$DEST" = /* ]] || { echo 'Destination must be absolute' >&2; exit 1; }
mkdir -p "$DEST"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
cd "$TEMP"
curl -fsSLO https://dl.k8s.io/release/v1.36.2/bin/linux/amd64/kubectl
curl -fsSLO https://dl.k8s.io/release/v1.36.2/bin/linux/amd64/kubectl.sha256
echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check
install -m 755 kubectl "$DEST/kubectl"
for SPEC in 'yannh/kubeconform v0.7.0 kubeconform-linux-amd64.tar.gz CHECKSUMS kubeconform' 'rhysd/actionlint v1.7.7 actionlint_1.7.7_linux_amd64.tar.gz actionlint_1.7.7_checksums.txt actionlint'; do
  read -r REPO VERSION ARCHIVE CHECKSUMS BINARY <<< "$SPEC"
  curl -fsSLO "https://github.com/$REPO/releases/download/$VERSION/$ARCHIVE"
  curl -fsSLO "https://github.com/$REPO/releases/download/$VERSION/$CHECKSUMS"
  awk -v archive="$ARCHIVE" '$2 == archive { print }' "$CHECKSUMS" > selected.sha256
  test -s selected.sha256
  sha256sum --check selected.sha256
  tar -xzf "$ARCHIVE" "$BINARY"
  install -m 755 "$BINARY" "$DEST/$BINARY"
done
