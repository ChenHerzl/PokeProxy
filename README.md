# PokeProxy DevOps Assignment

## Overview

PokeProxy accepts HMAC-signed Pokémon protobuf messages at `POST /stream`, applies
routing rules, and forwards matching Pokémon as JSON. This submission hardens the
application and provides containers, local Kubernetes, CI/CD, deployment verification,
rollback, monitoring, and a repeatable `make up` entry point.

For a 10–15 minute review, read this README, [rollback behavior](docs/rollback.md),
and [alert rationale](docs/alerts.md). The [planning index](docs/planning/README.md)
links implementation decisions and recorded evidence by phase.

## Architecture

```text
Client / verification Job
         |
         | POST /stream: protobuf + HMAC
         v
     PokeProxy --------------------> Redis
         |                    best-effort decoded-data cache
         | first matching rule
         | JSON + correlation ID
         v
   Mock downstream <-------------- verification Job checks exact receipts

     PokeProxy /metrics <--------- Prometheus <--------- Grafana
                                     |
                              five alert rules

GitHub Actions CI -> GHCR images + release artifact
                  -> promotion PR -> Git desired state
                                     |
                              local reconciler
                                     |
                              rollout + E2E gate
```

HMAC validation precedes cache access. A cache hit avoids decoding; it **still
forwards**. Rule conditions use AND, and the first matching rule wins. A valid
unmatched message returns HTTP 200 `{}` without a downstream call. A downstream
attempt has a finite deadline and no automatic POST retry.

## Repository structure

| Path | Purpose |
| --- | --- |
| `src/pokeproxy/` | Application, routing, cache, lifecycle, metrics and generated protobuf bindings |
| `mock_service/`, `config/`, `proto/` | Receipt-recording mock, native development rules and message schema |
| `tests/` | Application, real-process, delivery, monitoring and automation regression tests |
| `Dockerfile`, `uv.lock`, `pyproject.toml` | Separate proxy/mock image targets and locked Python dependencies |
| `infra/kind/` | Pinned local Kubernetes cluster configuration |
| `deploy/base/`, `deploy/overlays/` | Application manifests, manual local overlay and promoted release desired state |
| `deploy/monitoring/`, `deploy/verification/` | Prometheus/Grafana, dashboard, alerts and E2E Job |
| `.github/workflows/` | CI and separate desired-state promotion workflow |
| `scripts/`, `Makefile` | Bootstrap, reconciliation, verification, load generation and validation |
| `docs/issues/`, `docs/planning/`, `docs/verification/` | Findings/fixes, implementation records and evidence |

Generated `.local/`, `.kube/` and `.secrets/` directories are ignored by Git.

## Prerequisites

- Linux x86_64 or aarch64; Docker Engine with Buildx running and accessible to your user.
- At least **2 CPUs / 4 GiB RAM** available to Docker; approximately **10 GiB free disk** recommended.
- GNU Make, Bash, Python **3.11+**, standard Linux utilities including `timeout` and `flock`.
- kind **v0.33.0** and kubectl **1.36.x**. If missing, `make tools` explicitly downloads
  checksum-verified pinned binaries into `.local/bin`; it requires curl, sha256sum and install.
- Internet access for initial tool/image/dependency downloads.

Install host prerequisites using your normal OS setup. No target installs system
packages or uses sudo. `make doctor` checks prerequisites without creating a cluster.
Host uv, Redis, application Python packages and registry credentials are not needed
for `make up`. Linux x86_64 was tested; ARM64 was not exercised.

## Quick start

From the repository root:

```bash
make up
```

If Kubernetes CLIs are missing, run `make tools` first. Bootstrap checks prerequisites,
creates or starts the `pokeproxy` kind cluster, builds/loads both images, provisions
local credentials, deploys Redis/mock/proxy and monitoring, waits for readiness,
and runs E2E and monitoring gates. Any failed step returns non-zero.

It is safe to rerun: unchanged image content/configuration preserves workload
Pods, and credentials are retained. Access services in separate terminals:

| Command | Local URL |
| --- | --- |
| `make grafana` | http://127.0.0.1:3000/d/pokeproxy-health — anonymous Viewer |
| `make prometheus` | http://127.0.0.1:9090 — targets, queries and alerts |
| `make proxy` | http://127.0.0.1:8000/ready |

Tunnels bind localhost and run until Ctrl-C. `make help` lists all helpers.
After `make proxy`, test `/health`, `/ready`, `/metrics` and `/stats` at
`http://127.0.0.1:8000`. After `make prometheus`, inspect
http://127.0.0.1:9090/targets and http://127.0.0.1:9090/alerts.
These URLs require their tunnel; `make up` prints them but does not start
background port-forwards. Use `make verify` for signed `/stream` traffic.

Infrastructure images (Redis, Prometheus and Grafana) are also loaded through
host Docker, preserving the pinned references. This avoids dependence on registry
DNS inside kind nodes; host Docker still needs network access for uncached images.

## Verification

```bash
make status       # five Deployments should be available 1/1
make verify       # real traffic + downstream receipts + monitoring checks
make logs         # follow proxy logs; Ctrl-C to stop
```

`make verify` prints PASS only after the application gate and monitoring gate
succeed. An HTTP health check alone does not prove delivery. After `make proxy`,
`curl -fsS http://127.0.0.1:8000/ready` checks readiness independently.

Optional developer checks require uv and synchronize locked dependencies:

```bash
make lint
make test
```

Recorded local results: **114 passed, 1 skipped** (optional host Redis TTL fixture),
successful fresh-cluster `make down` → `make up`, successful second `make up` with
all five workload Pod UIDs unchanged, and a final passing `make verify`.
[Automation evidence](docs/planning/06-automation.md#validation-and-ai-assisted-flow)
records commands and environmental assumptions. Earlier CI-equivalent tests ran
with a real Redis fixture; [delivery evidence](docs/planning/04-cicd-gitops.md#local-validation-and-ai-assisted-flow)
distinguishes those results from hosted workflows, which were not executed here.

## Teardown

```bash
make down
```

Deletes only the named kind cluster; repeated teardown succeeds. Cluster data,
including mock receipts and monitoring history, is ephemeral. Local credentials,
downloaded CLIs and Docker caches remain for reuse. No unrelated Docker resources
are pruned.

## Application hardening

The [issue index](docs/issues/README.md) links each original problem, fix and test:

- Bounded uploads, downstream responses, concurrency and dependency deadlines;
  pooled HTTP connections without unsafe automatic retries.
- Direct Redis lookups, validated cache entries and bounded fallback when Redis fails.
- Startup validation of HMAC configuration and routing rules; corrected protobuf compatibility.
- Correct HTTP framing, filtered hop-by-hop metadata and isolated cookies/headers.
- Bounded latency histograms, consistent request accounting and safe structured logs.
- Graceful shutdown/client cleanup, bounded mock receipts and correlated real-traffic verification.

Configuration is documented in [.env.example](.env.example) and the
[hardening record](docs/planning/02-production-hardening.md). Kubernetes budgets
are explicit in [pokeproxy.env](deploy/base/pokeproxy.env). Rules load at startup;
configuration changes require a rollout. `/health` checks the process; `/ready`
checks initialization and reports the last cache-operation state, not continuous
Redis connectivity. Cache/downstream outages do not fail liveness.

## Containerization

The [multi-stage Dockerfile](Dockerfile) uses digest-pinned Python 3.13 and uv,
installs locked runtime dependencies, and produces `proxy` and `mock` targets.
Runtime images run as UID 10001 with one Uvicorn worker and finite shutdown budgets.
Dependency layers precede source copying for useful build caching.

`make build` builds both targets. Local bootstrap tags images using their full Docker
image IDs and loads them into kind. CI uses source-SHA/run/attempt tags and records
registry manifest digests; release deployments select those digests. These are
separate local-development and release paths.

## Kubernetes architecture

[Application manifests](deploy/base/) contain three single-replica Deployments
and ClusterIP Services in `pokeproxy`; Prometheus/Grafana run in `monitoring`.
Kustomize generates configuration hashes to trigger rollouts when settings change.
Secrets are provisioned outside Git.

Workloads define requests/limits, startup/readiness/liveness probes and termination
budgets. Containers use non-root identities, read-only root filesystems, dropped
capabilities and restricted privilege escalation. Application Pods do not mount
API tokens; Prometheus has scoped discovery RBAC. Redis and monitoring storage
are ephemeral. There is no ingress, TLS termination or NetworkPolicy enforcement
in this local assignment. See [infrastructure decisions](docs/planning/03-local-deployment.md).

## CI pipeline

[GitHub Actions CI](.github/workflows/ci.yml) runs on PRs, pushes to main/master,
and manual dispatch:

1. Checkout; configure Python 3.13 and pinned uv with dependency caching.
2. Ruff lint and pytest, including Redis TTL and real HTTP process tests.
3. Build wheel/source distribution.
4. Validate workflow/shell syntax, rendered Kubernetes schemas, Prometheus rules
   and dashboard JSON; run alert-rule scenarios.
5. Build both container targets using BuildKit caches.
6. On default-branch pushes, publish GHCR images with
   `sha-<full-git-sha>-<run-id>-<attempt>` tags and upload digest-bearing `release.json`.

Actions are pinned by commit. Credentials use scoped `GITHUB_TOKEN` references,
not literal secrets in YAML. PR/manual builds do not publish images. Hosted
publication and promotion require configuring this repository on GitHub and
were not executed in the local validation environment.

## CD / GitOps flow

1. Dispatch [promotion](.github/workflows/promote.yml) with a successful default-branch
   CI run ID. It verifies provenance and opens a PR updating `deploy/overlays/release`.
2. Review/merge the desired-state PR. The workflow does not access the cluster.
3. On the cluster host, fetch Git and explicitly reconcile the committed revision:

```bash
git fetch origin
python3 scripts/reconcile.py deploy --context kind-pokeproxy \
  --kubeconfig .kube/kind-config --revision origin/main
```

Use the repository's actual default branch. The release overlay starts as an
intentional placeholder: promote a real release before reconciliation. Application
Secrets must already exist; GHCR packages must be readable publicly or through an
externally provisioned pull Secret. GitHub must permit Actions to create PRs;
PRs created with `GITHUB_TOKEN` need the documented manual check trigger.
[Complete setup](docs/planning/04-cicd-gitops.md) covers these requirements.

Argo CD is deliberately not installed. This lightweight reconciler reads desired
state from Git but does not continuously correct drift. Argo would replace it,
watch the release overlay and run the verification Job as a PostSync hook. Never
run both deployment writers. `make up` builds the current working tree and refuses
to overwrite a cluster with a recorded GitOps release.

## Post-deploy verification

```bash
bash scripts/e2e-verify.sh --context kind-pokeproxy --kubeconfig .kube/kind-config
```

The gate launches a bounded Kubernetes Job using the deployed proxy image and
Secret. It signs a fixed matching protobuf payload, sends it twice through the
PokeProxy Service, and checks the mock for exact Pokémon JSON and routing reason
under unique request IDs. It also checks authenticated Redis, cache reuse,
readiness and application metrics. It does not clear shared mock history.
Failure or timeout exits non-zero. `make verify` additionally checks Prometheus
scraping/traffic metrics, loaded alerts, Grafana's dashboard and datasource.

## Rollback strategy

After a release passes rollout and E2E, the reconciler records the verified Git
revision and image digests in `pokeproxy-release-state`. A failed subsequent
release reapplies that Git snapshot, waits, and re-verifies it. The attempted
release still exits non-zero and is blocked from automatic reapplication.

An operator must revert/fix desired state in Git for durable recovery. A first
release has no fallback; failed recovery, interrupted reconciliation, secret
rotation and downstream side effects require intervention. No data rollback or
resource pruning occurs. `make up` failures retain resources for diagnosis and
do **not** invoke release rollback. With Argo, a live rollback alone would be
reverted by reconciliation; repair Git, and use one controller. See the
[rollback runbook](docs/rollback.md) for exact steps and limits.

## Observability

`/metrics` exposes counters for received/completed requests, HMAC/input rejection
outcomes, rule matches, downstream outcomes and Redis hit/miss/error/write outcomes;
histograms measure handler and downstream latency. An in-flight gauge measures
admitted work. Labels use bounded outcomes/statuses/rule indices, never payloads,
URLs or request IDs. Handler latency excludes response transmission.

[Prometheus](deploy/monitoring/prometheus/) discovers proxy Pods and evaluates five
alerts. [Grafana's 17-panel dashboard](deploy/monitoring/grafana/pokeproxy.json)
answers “Is PokeProxy healthy right now?” with traffic, failures, latency, cache,
scrape health and process CPU/RSS. It does not claim whole-cluster resource coverage.

[Alerts](docs/alerts.md) cover unavailable scraping, forwarding failures, server
errors, slow processing and cache errors, with thresholds, windows and operator
actions. Ratio alerts require sufficient traffic; individual invalid signatures,
normal cache misses and unmatched messages are intentionally not paged.
Alertmanager/notification delivery is not installed.

For a deeper check with 60 seconds of generated traffic and an intentional HMAC
rejection (requires uv), run:

```bash
uv run --frozen python scripts/verify_monitoring.py \
  --context kind-pokeproxy --kubeconfig .kube/kind-config
```

See [metric semantics and load evidence](docs/planning/05-observability.md) and the
[recorded monitoring result](docs/verification/part-4.json).

## Security considerations

Local HMAC/Redis/Grafana credentials are generated once, stored in ignored private
files, and preserved or recovered on reruns; mismatches fail rather than silently
rotate credentials. Kubernetes Secrets are not encrypted merely because their
values are base64 encoded. The public `.env.example` key is for native development
only; bootstrap generates its own credentials.

HMAC authenticates payloads but does not prevent replay. Redis writers and routing
configuration remain trusted. Logs omit payloads, signatures, credentials and raw
destination URLs. Keep metrics, mock administration and Grafana private: anonymous
Viewer access and plaintext internal traffic are local conveniences, not public
service security. Port-forward access requires authorized Kubernetes credentials.

## Design decisions and trade-offs

| Decision | Reason / cost |
| --- | --- |
| kind + Kustomize + Make | Small reproducible local stack; no cloud account, Terraform state or Helm dependency |
| Best-effort Redis cache | Forwarding survives cache failure; cache is not delivery deduplication |
| No automatic POST retries | Avoids hidden duplicate delivery; caller retries can still duplicate side effects |
| Manual Git reconciler | Demonstrates promotion, gating and recovery without Argo's local overhead; no continuous drift correction |
| Lightweight monitoring | Useful application signals without an operator/exporter stack; limited infrastructure visibility |
| Single worker and replicas | Predictable process-local counters/receipts; no HA or multi-worker aggregation claim |

## Production vs local assignment environment

Production would need a managed or resilient multi-node cluster, measured capacity,
multiple proxy replicas, disruption/topology policy and enforced network boundaries.
Replace local credentials with managed secret delivery/rotation, add TLS and proper
operator authentication, and use a controlled release reconciler such as Argo CD.

Retain immutable release artifacts and deployment audit history; add image scanning,
provenance/signing and policy enforcement. Define SLOs from real traffic, tune alerts,
route notifications, add cluster resource monitoring and durable monitoring storage.
Redis availability/persistence should reflect its cache-only contract. Replace the
mock with actual downstream integration tests and a documented delivery/idempotency
contract before claiming reliable business delivery.

## Known limitations

- One-node local cluster, ephemeral state; no HA, disaster-recovery or production capacity validation.
- No replay protection/exactly-once delivery; timeout can follow downstream acceptance.
- Single-process mock receipts are bounded and can be evicted; one fixed E2E route
  does not prove every rule or sustained throughput.
- Local reconciler has a host-local lock, no pruning and no continuous controller;
  monitoring does not automatically trigger rollback.
- Metrics reflect application-handler work, excluding server-level rejection before
  the handler; process RSS is not Pod memory/CPU throttling coverage.
- Hosted Actions/GHCR/promotion were not run here. Live rollback testing restored
  configuration using the same images; additional failure branches have unit coverage.
- Bootstrap reused Docker caches and local credentials after deleting the cluster;
  a pristine OS, ARM64 and stopped-node recovery were not separately tested.

## Future improvements

Prioritize downstream idempotency/replay policy, continuous Git reconciliation with
a reviewed Git-revert mechanism, supply-chain checks, and realistic load/SLO tests.
Add production networking, secrets and alert delivery when a target environment
exists; expand route coverage and multi-replica tests before scaling the service.

## Planning / implementation process

Development was AI-assisted: repository inspection, proposed changes, implementation,
tests and documentation were performed through an interactive coding assistant.
The [planning index](docs/planning/README.md) lists phases 01–06, prompt categories,
recorded decisions and validation limits. Historical assessments describe their
original point in time; they are not the current completion checklist. This final
pass reviewed source, manifests, workflows and existing evidence and reorganized
documentation without adding application features.
