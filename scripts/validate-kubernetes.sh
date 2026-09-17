#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
for OVERLAY in base overlays/local overlays/release verification monitoring monitoring/traffic; do
  echo "Validating deploy/$OVERLAY"
  kubectl kustomize "$ROOT/deploy/$OVERLAY" |
    kubeconform -strict -summary -kubernetes-version 1.36.0
done
