#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="$(awk '$1 == "image:" {print $2}' "$ROOT/deploy/monitoring/prometheus.yaml")"
docker run --rm --entrypoint /bin/promtool \
  -v "$ROOT/deploy/monitoring/prometheus:/etc/prometheus:ro" \
  "$IMAGE" check config --syntax-only /etc/prometheus/prometheus.yml
docker run --rm --entrypoint /bin/promtool \
  -v "$ROOT/deploy/monitoring/prometheus:/etc/prometheus:ro" \
  "$IMAGE" check rules /etc/prometheus/alerts.yml
docker run --rm --entrypoint /bin/promtool \
  -v "$ROOT/deploy/monitoring/prometheus:/etc/prometheus:ro" \
  "$IMAGE" test rules /etc/prometheus/alerts.test.yml
python3 -m json.tool "$ROOT/deploy/monitoring/grafana/pokeproxy.json" > /dev/null
