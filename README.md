# PokeProxy

A reverse proxy service that receives Pokemon data streams as protobuf-encoded payloads, validates HMAC signatures, matches against configurable routing rules, and forwards matching Pokemon as JSON to downstream services. It includes a Redis caching layer to avoid re-processing previously seen payloads.

## How It Works

```
[Client] --POST protobuf+HMAC--> [PokeProxy /stream]
                                       |
                                  1. Validate HMAC signature
                                  2. Check Redis cache
                                  3. Decode protobuf (on cache miss)
                                  4. Match against routing rules
                                  5. Convert to JSON
                                  6. Forward to downstream
                                       |
                                  [Downstream Service]
```

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Redis server running locally

## Quick Start

```bash
# Install dependencies
uv sync --frozen --dev

# Copy and configure environment
cp .env.example .env
# Edit .env with your settings

# Start Redis (if not already running)
redis-server &

# Start the mock downstream service
uv run --frozen uvicorn mock_service.main:app --host 127.0.0.1 --port 8001 &

# Start PokeProxy
uv run --frozen uvicorn pokeproxy.main:app --host 127.0.0.1 --port 8000 \
  --workers 1 --limit-concurrency 200 --timeout-graceful-shutdown 30
```

## Local Kubernetes deployment (Part 2)

Prerequisites: Docker running, kind, kubectl with Kustomize support, and Python 3.
Run from this repository root. The cluster image is pinned in
`infra/kind/cluster.yaml`. See [deployment decisions and verification](docs/planning/03-local-deployment.md).

```bash
mkdir -p .kube
kind create cluster --config infra/kind/cluster.yaml --kubeconfig .kube/kind-config
docker build --target proxy -t pokeproxy:part2 .
docker build --target mock -t pokeproxy-mock:part2 .
kind load docker-image --name pokeproxy pokeproxy:part2 pokeproxy-mock:part2
python3 scripts/create_local_secret.py
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy apply -f deploy/base/namespace.yaml
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy apply -f .secrets/kubernetes-secret.json
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy apply -k deploy/overlays/local
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n pokeproxy rollout status deployment/redis --timeout=180s
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n pokeproxy rollout status deployment/mock-downstream --timeout=180s
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n pokeproxy rollout status deployment/pokeproxy --timeout=180s
```

Skip cluster creation when the `pokeproxy` cluster already exists. Credentials
are generated once and preserved on reruns; do not commit `.secrets` or `.kube`.
Local image tags are for this demonstration. Rebuilding a tag does not restart
existing Pods; reload images and restart the affected Deployment, or use a new
image tag in the overlay.

Run the bounded end-to-end verification (the delete permits repeated runs):

```bash
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n pokeproxy delete job pokeproxy-verify --ignore-not-found
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy apply -k deploy/verification
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n pokeproxy wait --for=condition=complete job/pokeproxy-verify --timeout=90s
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n pokeproxy logs job/pokeproxy-verify
```

A failed wait is a failed verification; inspect Job logs and Pod events. The Job
checks actual downstream receipts as well as Redis authentication and cache use.
Services stay inside the cluster. For local access, run
`kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n pokeproxy port-forward service/pokeproxy 8000:8000`
and visit `http://127.0.0.1:8000/ready`. Stop the forwarding with Ctrl-C.
Teardown: `kind delete cluster --name pokeproxy`; this removes ephemeral cache
and receipt data while keeping your ignored local credentials for reuse.

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POKEPROXY_SECRET` | Yes | - | Base64-encoded HMAC secret, at least 32 decoded bytes |
| `POKEPROXY_CONFIG` | Yes | - | Path to rules JSON file |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection URL |

### Rules Config

Rules are loaded from the JSON file specified by `POKEPROXY_CONFIG`.

```json
{
  "rules": [
    {
      "url": "http://localhost:8001/pokemon",
      "reason": "strong fire pokemon",
      "match": ["type_one==Fire", "attack>80", "generation<4"]
    }
  ]
}
```

**Match operators:** `==`, `!=`, `>`, `<`

**Match logic:** All conditions in a rule must match (AND). First matching rule wins.

**Fields:** `number`, `name`, `type_one`, `type_two`, `total`, `hit_points`, `attack`, `defense`, `special_attack`, `special_defense`, `speed`, `generation`, `legendary`

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/stream` | POST | Proxy endpoint — validates, matches, forwards |
| `/health` | GET | Health check |
| `/ready` | GET | Initialized readiness and last cache operation health |
| `/stats` | GET | Per-rule cumulative forwarding summary |
| `/metrics` | GET | Prometheus application metrics |

## Load Generator

A load generator script is included to send synthetic Pokemon traffic:

```bash
uv run --frozen python scripts/load_generator.py --rps 10 --duration 60
```

Options:
- `--url` — Target URL (default: `http://localhost:8000/stream`)
- `--rps` — Requests per second (default: 10)
- `--duration` — Duration in seconds, 0 for infinite (default: 60)
- `--secret` — Base64-encoded HMAC secret (default: test secret)

## Testing

```bash
uv run --frozen pytest -v
```

## Startup configuration and dependency verification

Run commands from the repository root. `POKEPROXY_SECRET` is the supported
secret variable; `POKEPROXY_HMAC_KEY` is not supported. The value must be strict
base64 encoding of at least 32 bytes. The public key in `.env.example` and the
load generator is only for local demonstrations. Generate a random key with:

```bash
python3 -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'
```

Store it in `.env` (ignored by Git) and use the same key for clients. The length
check does not measure entropy. Settings representations and human-readable
validation errors hide secret values; do not log raw environment variables or
Pydantic `ValidationError.errors()` / `.json()`, which can include input values.
Set the listener port with Uvicorn's `--port`; `POKEPROXY_PORT` has been removed
because it never controlled the server.

Install using `uv sync --frozen --dev` and run `uv run --frozen pytest -v`.
The protobuf runtime minimum is 6.31.1 to match the generated code; the lock file
selects the exact normal development/runtime dependencies. Regenerate protobuf
files with `sh scripts/generate_proto.sh` (requires uv and package-index access).
The isolated generator is pinned to `grpcio-tools==1.74.0`; review generated diffs
and rerun tests after changing the schema.

See [step 1 verification](docs/verification/step-1.md) for results and scope,
[review findings](docs/issues/README.md) for remaining work, and
[planning decisions](docs/planning/part-1-review.md).


## Production hardening (Part 1)

See [implemented decisions and results](docs/planning/02-production-hardening.md)
and [individual issue records](docs/issues/README.md). Kubernetes, CI/CD and the
Prometheus/Grafana deployment are subsequent work.

The proxy validates configuration before serving requests. Rules are read once;
restart after changing the file. Top-level JSON must contain only `rules`, and
rules require `url`, `reason`, and `match`. An explicitly empty list is allowed;
a missing list is an error. Reasons must be printable ASCII, at most 1024
characters. URLs need an HTTP(S) host and cannot contain credentials or fragments.
AND conditions, first match, numeric JSON fields, and unmatched HTTP 200 `{}` are
unchanged. Cache hits still forward: Redis avoids decoding, not delivery.

Forwarding uses one pooled HTTP request, with no automatic POST retry. Timeouts
return 504, transport/oversized-response failures return 502. Ambiguous failures
can occur after downstream acceptance; caller retries can still duplicate work.
Responses preserve raw downstream bytes and Content-Encoding while recalculating
Content-Length; the response limit applies to encoded bytes, not decompressed
client memory. Hop-by-hop metadata and proxy-owned request headers are filtered.
The HTTP pool does not retain cookies between callers and ignores environment
proxy settings (`trust_env=False`). Configure routing URLs explicitly.

Redis is a best-effort decoded-data cache. Read/write errors and corrupt entries
fall back to decoding/forwarding within a per-operation budget. Redis URL query
options are forbidden so they cannot override timeouts/pool settings. Redis must
be trusted and network-restricted: schema validation does not authenticate cached
content against the original signed payload.

### Operational settings

All application limits are positive and loaded from the environment or `.env`.
Non-finite time values are rejected. Environment values take precedence.

| Variable | Default | Meaning |
| --- | --- | --- |
| `POKEPROXY_HTTP_TIMEOUT` | 5 | Each HTTP connect/read/write/pool inactivity timeout, seconds |
| `POKEPROXY_DOWNSTREAM_DEADLINE` | 10 | Total downstream attempt budget, seconds |
| `POKEPROXY_UPLOAD_TIMEOUT` | 10 | Total request body read budget, seconds |
| `POKEPROXY_REDIS_TIMEOUT` | 0.25 | Total budget for each Redis read or write, seconds |
| `POKEPROXY_CACHE_TTL` | 300 | Cached decoded JSON TTL, seconds |
| `POKEPROXY_MAX_BODY_BYTES` | 1048576 | Maximum accumulated inbound protobuf bytes |
| `POKEPROXY_MAX_RESPONSE_BYTES` | 1048576 | Maximum raw downstream response bytes |
| `POKEPROXY_MAX_INFLIGHT` | 100 | Concurrent admitted `/stream` handlers and client pool capacities |
| `POKEPROXY_CLOSE_TIMEOUT` | 2 | Budget for closing each shared client, seconds |

Admission rejects excess work immediately with 503. The application cap ends when
its handler returns; Uvicorn's separate concurrency limit also bounds outstanding
ASGI responses/slow clients. Run one worker per process; metrics, admission, and
mock receipts are process-local. Choose limits from measured capacity rather than
assuming these defaults are a production sizing result.

### Health, termination and diagnostics

`/health` reports the live process. `/ready` requires completed initialization;
cache health is `unknown`, `healthy`, or `degraded` based on the last operation,
not a continuous connectivity probe. Cache/downstream outages do not fail liveness
or automatically remove a functioning proxy from service.

Uvicorn handles SIGTERM/SIGINT: it stops accepting connections, drains in-flight
work, then exits the lifespan and closes clients. Do not install a competing
application signal handler. Use a finite graceful-shutdown timeout; the example
30 seconds covers the default 10-second upload, two 0.25-second cache operations,
and 10-second downstream budget. Each client close adds up to 2 seconds; allow
additional process termination grace outside Uvicorn. Raising budgets requires
revisiting this timing. Shutdown tests cover both signals during a hung downstream.

Application logs are JSON on stdout with event, safe request ID, outcome and
context. HTTP responses include `X-Request-ID`; generated error JSON also includes
`request_id`. IDs are correlation data, not authenticated identities. Arbitrary
client IDs are accepted only within the documented safe character/length bounds.
Raw payloads, signatures, keys, exception text and destination URLs are omitted.
Uvicorn's own server/access logs retain its standard format. `/metrics`, `/stats`
and mock administration endpoints should be private in the later deployment.

`/metrics` exports request/forwarding counters, duration histograms in seconds,
cache outcome counters and current admitted work. Labels are bounded outcomes,
statuses and `rule_0`, `rule_1`, etc. Rule indices refer to the startup ordering;
reordering rules changes their meaning across releases. `/stats` now uses these
IDs instead of URLs. `bytes_sent` counts attempted JSON payload bytes, not proven
socket delivery. Infrastructure CPU/memory collection, scraping, dashboards and
alerts are not implemented in Part 1.

### Mock and end-to-end verification

The mock requires one worker. It retains at most `MOCK_MAX_RECEIPTS` (default
1000); `MOCK_MAX_BODY_BYTES` defaults to 1048576 and `MOCK_UPLOAD_TIMEOUT` to 10
seconds. These are environment settings, loaded at startup. Receipt state is lost
on restart, and old receipts are evicted; size the retention for demonstration
traffic. GET `/received?request_id=...` isolates a run. DELETE remains available
for manual reset, but the verifier never uses it.

With Redis, mock and proxy running as above, export the same HMAC secret used by
the proxy (for the checked-in local example only, `. ./.env; export POKEPROXY_SECRET`
loads it in a POSIX shell), then run:

```bash
uv run --frozen python scripts/verify.py
# Optional: --proxy-url http://127.0.0.1:8000 --mock-url http://127.0.0.1:8001
```

The verifier signs a fixed Charizard protobuf and checks the complete downstream
JSON plus expected reason and exactly one correlated receipt. HTTP 200 alone
cannot pass it. Use `--reason` if the intentionally configured matching reason
changes. It exits nonzero on HTTP error, missing receipt or mismatched content.
The load generator is separate: it remains sequential, reports achieved RPS,
validates rate/duration, exits nonzero on HTTP failures, and reads
`POKEPROXY_SECRET` before falling back to the public development key. Prefer that
environment variable to `--secret`, whose argument can appear in process listings.

### Running the expanded tests

```bash
uv sync --frozen --dev
uv run --frozen ruff check .
uv run --frozen pytest -q
# Also run the real Redis TTL check against an isolated instance:
TEST_REDIS_URL=redis://127.0.0.1:6379/0 uv run --frozen pytest -q
```

The full suite includes local socket/process tests and starts temporary Uvicorn
processes; it needs permission to bind localhost ports and send process signals.
Only the real Redis test skips when `TEST_REDIS_URL` is unset. It uses a unique
key and removes it, never FLUSHDB. See the hardening plan for the actual verified
Redis image and command results.
