# Part 4 — observability

Implemented: 2026-09-17. The user requested control-flow-driven metrics, a small
Prometheus/Grafana deployment, Git-stored dashboards/alerts, and live traffic
verification. Existing Part 1 metrics were inspected before making changes.

## Control flow and metric meaning

`POST /stream` checks admission, reads a bounded body, validates the HMAC, looks
up decoded data in Redis, decodes/writes on a miss, chooses the first matching
rule, and optionally performs one downstream HTTP attempt. A cache hit still
routes and forwards. An unmatched valid Pokémon returns HTTP 200 `{}`. Neither
a cache miss nor an unmatched message is an application failure.

The existing `StatsRegistry` already tracked completions, forwarding outcomes,
cache operations, handler/forward histograms and admitted in-flight work. These
were retained. Added received-request and rule-decision counters and a Linux
`ProcessCollector` in the same isolated per-lifespan registry. Histogram buckets
now cover the configured upload/downstream budgets instead of stopping at ten
seconds before `+Inf`. No duplicate generic “success” counter was added: success
is a precise selection of existing completion outcomes.

| Metric | Type and labels | Increment/observation point and meaning |
| --- | --- | --- |
| `pokeproxy_requests_received_total` | Counter, none | Entry to the `/stream` handler, before admission/body/authentication; not necessarily a decoded message |
| `pokeproxy_requests_total` | Counter, `outcome`, `status` | Handler `finally`, once per completion/rejection/cancellation; successes are `forwarded` and `unmatched` |
| same counter, `outcome="invalid_signature"` | Counter selection | Missing, duplicate, malformed or mismatched HMAC after the bounded body read; does not access Redis or forward |
| same counter, `outcome="invalid_protobuf"` | Counter selection | Authenticated body fails decoding after a cache miss/error |
| `pokeproxy_rule_matches_total` | Counter, `rule` | At first-match routing decision, before forwarding; no match is recorded by the `unmatched` completion outcome |
| `pokeproxy_forward_total` | Counter, `rule`, `outcome` | Forwarding `finally`; one application attempt, with no automatic POST retry; success means downstream HTTP <400, not proven side effects |
| `pokeproxy_cache_total` | Counter, `outcome` | Reads: `hit`, `miss`, `read_error`; writes: `write`, `write_error`; corrupt entries are errors, not successful misses |
| `pokeproxy_request_duration_seconds` | Histogram, none | Finished-handler wall time including body read, cache and forwarding; excludes response transmission |
| `pokeproxy_forward_duration_seconds` | Histogram, `rule` | Downstream attempt through bounded raw response read, including failures/timeouts; serialization occurs before this timer |
| `pokeproxy_inflight` | Gauge, none | Current admitted handlers; increments after admission and decrements on all exit paths |
| `process_cpu_seconds_total` | Counter, none | Cumulative CPU time of the one proxy process; `rate()` gives CPU cores |
| `process_resident_memory_bytes`, `process_start_time_seconds`, file descriptor metrics | Gauges, none | Linux `/proc` process observations; not Pod/cgroup usage, limits or restart counters |

The exporter remains conventional `GET /metrics` with Prometheus content type.
Health/readiness/stats/metrics requests do not inflate stream traffic metrics.
Uvicorn can reject requests at its own concurrency boundary before the handler;
those are outside these application counters. Cancellation and disconnect
outcomes are recorded but may not produce a response delivered to the client.
The service's historical HTTP <400 success classification includes redirects.

Labels contain fixed outcome names, HTTP statuses and configured `rule_N`
indices. No request IDs, payload fields, URLs, exception text, cache keys or
user-supplied values become metric labels. Rule labels grow with trusted startup
configuration, not request traffic; reordering rules changes their meaning.
Prometheus adds only job/instance/Pod/namespace target identity. One Uvicorn
worker per Pod is required; aggregation across Pods happens in PromQL. Received
and finished counts may differ during live work or a process crash.

See [Prometheus instrumentation guidance](https://prometheus.io/docs/practices/instrumentation/)
and the [Python process collector](https://prometheus.github.io/client_python/collector/).

## Lightweight Kubernetes design

`deploy/monitoring` creates one Prometheus Deployment and one Grafana Deployment
in `monitoring`, with ClusterIP Services, probes, resource requests/limits,
non-root users, dropped capabilities and read-only roots. Writable directories
use bounded `emptyDir` volumes. Images are version- and digest-pinned:
Prometheus 3.14.0 and Grafana 13.2.2. These are reproducible selections, not a
promise that future security updates will be unnecessary.

Prometheus scrapes every 15 seconds and evaluates rules every 15 seconds. Pod
discovery selects only `app.kubernetes.io/name=pokeproxy` in `pokeproxy` and its
named HTTP port. It deliberately scrapes each Pod IP, not a load-balanced Service:
sampling different replicas under one target would corrupt counter-rate meaning.
A namespaced Role allows only Pod get/list/watch; only Prometheus mounts its
ServiceAccount token. No cluster-wide RBAC, operator, ServiceMonitor CRDs,
kube-state-metrics, node exporter, cAdvisor scrape, or Alertmanager is installed.
The sample limit is 10,000 per target, a guard against accidental metric growth.

Prometheus retains at most 24 hours or 512 MB of persisted blocks. Its 1 GiB
volume also leaves room for WAL/head data; retention size alone is not a hard
total disk cap. History is lost on Pod replacement. Grafana data is ephemeral
too, but its datasource, provider and dashboard are recreated from Git-managed
ConfigMaps. Kustomize hashes configuration names so `apply -k` changes restart
the consuming Pods. Production would need durable storage and appropriate HA.

Grafana is anonymous **Viewer** for this localhost demonstration, with sign-up
and the login form disabled. A random admin password is generated into an ignored
mode-0600 file and injected through a Secret, so default admin credentials are
not left enabled. No password is needed to view the dashboard. Both UIs have
only ClusterIP exposure; port-forward binds localhost. Public exposure would
require deliberate authentication/TLS changes.

## Deploy or update locally

Prerequisites are the Part 2 kind cluster/application Secret, Docker, kind,
kubectl and Python 3.13. From the repository root:

```bash
docker build --target proxy -t pokeproxy:part4 .
kind load docker-image --name pokeproxy pokeproxy:part4
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy apply -k deploy/overlays/local
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n pokeproxy rollout status deployment/pokeproxy --timeout=180s
python3 scripts/create_monitoring_secret.py
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy apply -f deploy/monitoring/namespace.yaml
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy apply -f .secrets/grafana-secret.json
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy apply -k deploy/monitoring
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n monitoring rollout status deployment/prometheus --timeout=180s
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n monitoring rollout status deployment/grafana --timeout=240s
```

The mock keeps its existing local `part2` image; only the proxy image changes.
The `part4` tag is a local convenience. CI still produces unique SHA-based tags
and digest-pinned release artifacts; production promotion follows Part 3. When
rebuilding the same local tag, reload it and restart the proxy Deployment because
Kubernetes does not restart Pods merely because a local tag's contents changed.
Monitoring is a separate infrastructure apply; the Part 3 application reconciler
does not deploy or roll back this monitoring overlay. Part 5 will automate setup.

## Access Grafana and Prometheus

Run each command in a separate terminal and leave it running:

```bash
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n monitoring port-forward --address 127.0.0.1 service/grafana 3000:3000
```

Open **http://127.0.0.1:3000/d/pokeproxy-health**. No login is required (Viewer).

```bash
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n monitoring port-forward --address 127.0.0.1 service/prometheus 9090:9090
```

Open **http://127.0.0.1:9090**, with Targets at `/targets` and alerts at `/alerts`.
Ctrl-C stops a tunnel without stopping the workloads. Use another local port if
these are occupied, and pass matching URL flags to the verifier below.

## Dashboard and alerts

[The provisioned dashboard](../../deploy/monitoring/grafana/pokeproxy.json) has
17 panels: scrapeable proxy Pods, received RPS, successful handling and server
error ratios; traffic and handler outcomes; handler/downstream p50/p95 latency;
rule decisions and forwarding outcomes; cache operation rates and hit ratio;
in-flight work; process CPU, resident memory and uptime; pending/firing alerts.

Its descriptions explain populations and limitations. Histogram buckets are
aggregated across Pods before quantile calculation. Grafana's rate interval uses
the configured 15-second scrape interval. Idle ratios/quantiles are undefined,
so they show no traffic/data instead of a fabricated 100% success. A zero traffic
panel is not proof of health: always check scrape availability. Process resources
are explicitly named; no dashboard panel pretends that process RSS equals Pod
working set or that a scrape is a Kubernetes readiness probe.

Five Git-managed alerts cover unavailable scrapes, forwarding failures, handler
server errors, slow processing and persistent cache errors. Their thresholds,
lookback/hold windows, justifications and operator actions are documented in
[alerts.md](../alerts.md), including deliberately unalerted conditions. Rules are
evaluated and visible in Prometheus; external notification delivery is out of scope.

## Real-traffic verification

With both localhost tunnels running:

```bash
uv run --frozen python scripts/verify_monitoring.py \
  --context kind-pokeproxy --kubeconfig .kube/kind-config \
  --output /tmp/pokeproxy-observability.json
```

The verifier first confirms Pod discovery and process metrics. It starts a
bounded in-cluster Job using the exact deployed proxy image and a Secret reference.
The Job runs the provided load generator at 5 RPS for 60 seconds, then sends one
intentionally invalid signature and requires HTTP 401. No HMAC value is copied
to host arguments or output. It checks Prometheus counter/histogram-count deltas,
cache hits and absence of cache errors; rejects a concurrent rollout that could
invalidate counter comparisons; checks five healthy alert rules; retrieves the
provisioned dashboard; executes every panel expression; and verifies Grafana can
query Prometheus through its datasource. All failures exit nonzero.

The Job's wall-clock deadline is 90 seconds with no retries. Wrapper polling and
HTTP/API calls are also bounded. Finished Jobs expire after ten minutes. Tests
use random selection among the generator's fixed Pokémon; exact rule/miss counts
vary with cache warmth and selection. The full matching-payload E2E deployment
gate remains separate and can be run with `scripts/e2e-verify.sh`.

Actual live results are recorded in [part-4.json](../verification/part-4.json).
The generator achieved **4.97 RPS**: 298 signed requests with zero errors, plus
one deliberately rejected HMAC request. Prometheus observed:

| Signal | Increase during the run |
| --- | --- |
| Received and finished handlers | 299 each |
| Successful forwarded handlers / rule matches / successful attempts | 153 each |
| Valid unmatched handlers | 145 |
| Invalid HMAC rejections | 1 |
| Cache hits / misses / errors | 286 / 12 / 0 |
| Handler / forwarding histogram observations | 299 / 153 |

One proxy target was scrapeable; all 20 dashboard queries executed successfully
and all five alert rules reported healthy evaluation. At the recorded instant,
the one-second handler bucket contained all 299 observations, process RSS was
72,695,808 bytes, and one-minute CPU use was about 0.0124 cores. Histogram p95
estimates were about 4.8 ms (interpolation inside the first 5 ms bucket).
They are observations of this local run, not a performance benchmark or production
capacity claim. A traffic run makes latency histograms and process resource
series visible; alert firing thresholds are tested deterministically with
synthetic time series rather than causing extended live dependency outages.

## Validation and implementation flow

The assistant inspected `proxy.py`, `cache.py`, lifecycle/registry setup, existing
tests and the load generator; checked primary Prometheus/Grafana documentation;
added only missing control-flow instrumentation; built the lightweight manifests,
dashboard and alert rules; and exercised them against kind. No subagents were used.

- Application regression: **107 passed, 1 skipped** (standalone Redis TTL fixture
  was not configured; deployed Redis is exercised by the traffic/E2E Jobs).
- New metrics tests prove started versus finished counts, cancellation cleanup,
  invalid authentication/protobuf boundaries, first-match/no-match behavior, cache
  hit forwarding, nonfatal cache errors, and downstream 4xx classification.
- Ruff, actionlint, shell syntax and Kubernetes schema validation passed; **47
  rendered resources** across all six configurations were valid.
- `bash scripts/validate-monitoring.sh` passed configuration syntax, all five alert
  definitions, nine promtool scenarios and dashboard JSON syntax. CI now runs it.
- The instrumented proxy Docker target built and rolled out successfully.
- The existing protobuf-to-downstream E2E deployment gate also passed after the
  instrumentation rollout. Both monitoring Deployments are Ready. Temporary
  verification port-forwards were stopped; use the access commands above.

To remove only monitoring, use
`kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy delete -k deploy/monitoring`.
This removes the monitoring namespace and its ephemeral history/admin Secret,
plus the discovery Role/RoleBinding in `pokeproxy`; the application is left
running. The ignored local Grafana credential file remains for reuse.

Only the compact monitoring stack and application signals are added. Persistent
storage, whole-cluster resource exporters, notification routing, automated
monitoring-driven rollback, and one-command bootstrap remain separate work.
