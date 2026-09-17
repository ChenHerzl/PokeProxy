#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$ROOT/.local/bin:$PATH"
export KIND_EXPERIMENTAL_PROVIDER=docker
KIND_BIN="${KIND_BIN:-kind}"
CLUSTER=pokeproxy
KUBE_FILE="$ROOT/.kube/kind-config"
KUBE=(kubectl --kubeconfig "$KUBE_FILE" --context kind-pokeproxy --request-timeout=20s)
PHASE=initialization
trap 'failure_status=$?; printf "ERROR during %s (line %s, exit %s). See the error above; resources are retained for diagnosis.\n" "$PHASE" "$LINENO" "$failure_status" >&2; exit "$failure_status"' ERR

fail() { echo "ERROR: $*" >&2; exit 1; }
step() { PHASE="$*"; printf '\n==> %s\n' "$PHASE"; }
need() { command -v "$1" >/dev/null || fail "Missing prerequisite '$1'. See README prerequisites; 'make tools' installs only pinned kind/kubectl locally."; }

basic_checks() {
  [[ "$(uname -s)" == Linux ]] || fail 'This entry point supports Linux.'
  need timeout
  need flock
  need python3
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required on the host; the application uses containerized Python 3.13.")'
}

docker_check() {
  need docker
  timeout 20 docker info --format '{"OSType":"{{.OSType}}","Architecture":"{{.Architecture}}","NCPU":{{.NCPU}},"MemTotal":{{.MemTotal}}}' > .local/docker-info.json
  python3 - <<'PY'
import json
from pathlib import Path
data = json.loads(Path('.local/docker-info.json').read_text())
if data['OSType'] != 'linux' or data['Architecture'] not in ('x86_64', 'aarch64'):
    raise SystemExit('ERROR: Docker must run Linux amd64/arm64 containers.')
if data['NCPU'] < 2 or data['MemTotal'] < 4 * 1024**3:
    raise SystemExit('ERROR: Allocate at least 2 CPUs and 4 GiB RAM to Docker for this stack.')
print(f"Docker ready: {data['NCPU']} CPUs, {data['MemTotal'] / 1024**3:.1f} GiB RAM")
PY
  timeout 20 docker buildx version >/dev/null
}

cluster_tools() {
  need "$KIND_BIN"
  need kubectl
  local version
  version="$(timeout 15 "$KIND_BIN" version)"
  [[ "$version" == 'kind v0.33.0 '* ]] || fail 'Use kind v0.33.0 for the pinned node image; run make tools.'
  timeout 15 kubectl version --client -o json | python3 -c '
import json, sys
v=json.load(sys.stdin)["clientVersion"]
if v["major"] != "1" or v["minor"] != "36":
    raise SystemExit("ERROR: Use kubectl 1.36.x for this cluster; run make tools.")'
}

lock_operation() {
  mkdir -p .local
  exec 9>.local/operation.lock
  flock -n 9 || fail 'Another bootstrap/build/teardown is running in this checkout.'
}

ensure_cluster() {
  step 'Create or start the local kind cluster'
  local clusters nodes node
  clusters="$(timeout 30 "$KIND_BIN" get clusters)"
  if [[ $'\n'"$clusters"$'\n' == *$'\n'"$CLUSTER"$'\n'* ]]; then
    nodes="$(timeout 30 "$KIND_BIN" get nodes --name "$CLUSTER")"
    [[ -n "$nodes" ]] || fail 'Cluster exists without nodes; run make down, then make up.'
    while IFS= read -r node; do
      if [[ "$(timeout 20 docker inspect --format '{{.State.Running}}' "$node")" != true ]]; then
        timeout 60 docker start "$node"
      fi
    done <<< "$nodes"
    echo 'Reusing existing cluster; refreshing the checkout-local kubeconfig.'
  else
    timeout 600 "$KIND_BIN" create cluster --name "$CLUSTER" \
      --config infra/kind/cluster.yaml --kubeconfig "$KUBE_FILE" --wait 180s
  fi
  timeout 30 "$KIND_BIN" get kubeconfig --name "$CLUSTER" > "$KUBE_FILE.tmp"
  chmod 600 "$KUBE_FILE.tmp"
  mv "$KUBE_FILE.tmp" "$KUBE_FILE"
  local deadline=$((SECONDS + 120))
  until timeout 10 "${KUBE[@]}" get --raw=/readyz > /dev/null 2>.local/api-ready-error.log; do
    (( SECONDS < deadline )) || { cat .local/api-ready-error.log >&2; fail 'Kubernetes API did not become ready in 120s.'; }
    sleep 2
  done
  timeout 200 "${KUBE[@]}" wait --for=condition=Ready nodes --all --timeout=180s --request-timeout=190s
}

build_images() {
  step 'Build proxy and mock images (Docker layer cache enabled)'
  timeout 900 docker build --target proxy --iidfile .local/proxy.iid .
  timeout 900 docker build --target mock --iidfile .local/mock.iid .
  local proxy_id mock_id
  proxy_id="$(<.local/proxy.iid)"
  mock_id="$(<.local/mock.iid)"
  [[ "$proxy_id" =~ ^sha256:[a-f0-9]{64}$ && "$mock_id" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'Docker returned an invalid image ID.'
  PROXY_IMAGE="pokeproxy:local-${proxy_id#sha256:}"
  MOCK_IMAGE="pokeproxy-mock:local-${mock_id#sha256:}"
  timeout 20 docker tag "$proxy_id" "$PROXY_IMAGE"
  timeout 20 docker tag "$mock_id" "$MOCK_IMAGE"
  python3 scripts/local_config.py images "$PROXY_IMAGE" "$MOCK_IMAGE"
}

rollout() {
  echo "Waiting for $1/deployment/$2"
  if ! timeout 320 "${KUBE[@]}" -n "$1" rollout status "deployment/$2" --timeout=300s --request-timeout=310s; then
    echo 'Rollout failed. Pod status and recent events:' >&2
    timeout 30 "${KUBE[@]}" -n "$1" get pods || echo 'Could not fetch Pods.' >&2
    timeout 30 "${KUBE[@]}" -n "$1" get events --sort-by=.lastTimestamp || echo 'Could not fetch events.' >&2
    fail "Deployment $1/$2 did not become ready; resolve the reported error and rerun make up."
  fi
}

verify_stack() {
  step 'Verify real protobuf/HMAC forwarding and correlated downstream receipts'
  timeout 180 python3 scripts/reconcile.py verify --context kind-pokeproxy --kubeconfig "$KUBE_FILE"
  step 'Verify Prometheus scraping, alert rules and Grafana provisioning'
  timeout 150 python3 scripts/check_stack.py --kubeconfig "$KUBE_FILE"
}

access() {
  cat <<'TEXT'

PokeProxy and monitoring are ready. Run each tunnel in a separate terminal:
  make grafana     -> http://127.0.0.1:3000/d/pokeproxy-health (Viewer, no login)
  make prometheus  -> http://127.0.0.1:9090 (targets and alerts)
  make proxy       -> http://127.0.0.1:8000/ready

After starting those tunnels, test:
  http://127.0.0.1:8000/health       process health
  http://127.0.0.1:8000/ready        application readiness
  http://127.0.0.1:8000/metrics      Prometheus application metrics
  http://127.0.0.1:8000/stats        per-rule forwarding statistics
  http://127.0.0.1:9090/targets      scrape target health
  http://127.0.0.1:9090/alerts       alert state
  curl -fsS http://127.0.0.1:8000/ready
Use make verify to test signed POST /stream traffic and downstream delivery.
The URLs become reachable after their tunnel starts; no tunnels run in the background.

Useful commands: make status, make logs, make verify
Teardown: make down (deletes only kind cluster pokeproxy; keeps local credentials and image caches)
TEXT
}

ACTION="${1:-help}"
case "$ACTION" in
  help)
    cat <<'TEXT'
make up          Check prerequisites, create/start kind, build/load/deploy all workloads, verify
make down        Delete the pokeproxy cluster; preserve credentials, tools and Docker caches
make tools       Explicitly download pinned kind/kubectl into .local/bin; no system packages
make doctor      Check bootstrap prerequisites and Docker access
make build       Build both images and generate a local overlay with content-derived tags
make verify      Run real-traffic E2E and check monitoring (no host Python packages required)
make status      Show application and monitoring workloads
make logs        Follow the proxy logs
make grafana     Forward Grafana to localhost:3000
make prometheus  Forward Prometheus to localhost:9090
make proxy       Forward PokeProxy to localhost:8000
make test        Run pytest via uv (additional prerequisite)
make lint        Run Ruff via uv (additional prerequisite)
TEXT
    exit 0 ;;
  test|lint)
    need uv
    echo 'uv will synchronize locked project dependencies and its managed Python if needed.'
    if [[ "$ACTION" == test ]]; then exec uv run --frozen pytest -q; fi
    exec uv run --frozen ruff check . ;;
  up|doctor|down|build)
    step 'Check prerequisites'
    basic_checks
    need docker
    if [[ "$ACTION" == up || "$ACTION" == doctor ]]; then cluster_tools; fi
    if [[ "$ACTION" == down ]]; then need "$KIND_BIN"; fi
    lock_operation
    if [[ "$ACTION" == down ]]; then
      timeout 20 docker info --format '{{.ServerVersion}}' >/dev/null
    else
      docker_check
    fi
    if [[ "$ACTION" == doctor ]]; then echo 'Prerequisites passed.'; exit 0; fi
    ;;
  verify|status|logs|grafana|prometheus|proxy)
    need kubectl
    need timeout
    [[ -f "$KUBE_FILE" ]] || fail 'No checkout-local kubeconfig. Run make up first.'
    ;;
  *) fail "Unknown action: $ACTION. Run make help." ;;
esac

case "$ACTION" in
  up)
    mkdir -p .kube
    chmod 700 .kube
    ensure_cluster
    [[ -z "$(timeout 30 "${KUBE[@]}" -n pokeproxy get configmap pokeproxy-release-state --ignore-not-found -o name)" ]] || fail 'This cluster has a Part 3 release record. Use the release reconciler, or make down before switching to local bootstrap.'
    build_images
    step 'Load both content-tagged images into kind'
    timeout 300 "$KIND_BIN" load docker-image --name "$CLUSTER" "$PROXY_IMAGE" "$MOCK_IMAGE"
    step 'Preload pinned Redis, Prometheus and Grafana images through host Docker'
    KIND_BIN="$KIND_BIN" python3 scripts/load-dependency-images.py
    step 'Prepare namespaces and preserve credentials'
    timeout 60 "${KUBE[@]}" apply -f deploy/base/namespace.yaml -f deploy/monitoring/namespace.yaml
    python3 scripts/local_config.py secrets --kubeconfig "$KUBE_FILE"
    timeout 60 "${KUBE[@]}" apply -f .secrets/kubernetes-secret.json -f .secrets/grafana-secret.json
    step 'Deploy Redis, mock downstream and PokeProxy'
    timeout 120 "${KUBE[@]}" apply -k .local/deploy
    rollout pokeproxy redis
    rollout pokeproxy mock-downstream
    rollout pokeproxy pokeproxy
    step 'Install Prometheus, Grafana, dashboards and alerts'
    timeout 120 "${KUBE[@]}" apply -k deploy/monitoring
    rollout monitoring prometheus
    rollout monitoring grafana
    verify_stack
    access ;;
  down)
    step 'Delete only the pokeproxy kind cluster'
    timeout 180 "$KIND_BIN" delete cluster --name "$CLUSTER" --kubeconfig "$KUBE_FILE"
    # Remove only this managed kubeconfig; keep credentials/cache for reuse.
    python3 -c 'from pathlib import Path; Path(".kube/kind-config").unlink(missing_ok=True)'
    echo 'Cluster removed (or already absent). Local secrets, tools and Docker caches retained.' ;;
  build) build_images ;;
  verify) basic_checks; lock_operation; verify_stack ;;
  status)
    timeout 30 "${KUBE[@]}" -n pokeproxy get deployments,pods,services,jobs
    timeout 30 "${KUBE[@]}" -n monitoring get deployments,pods,services ;;
  logs) exec "${KUBE[@]}" -n pokeproxy logs deployment/pokeproxy --tail=100 -f --request-timeout=0 ;;
  grafana) exec "${KUBE[@]}" -n monitoring port-forward --address 127.0.0.1 service/grafana 3000:3000 --request-timeout=0 ;;
  prometheus) exec "${KUBE[@]}" -n monitoring port-forward --address 127.0.0.1 service/prometheus 9090:9090 --request-timeout=0 ;;
  proxy) exec "${KUBE[@]}" -n pokeproxy port-forward --address 127.0.0.1 service/pokeproxy 8000:8000 --request-timeout=0 ;;
esac
