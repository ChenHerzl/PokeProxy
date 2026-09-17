# Part 5 — zero-to-running automation

Implemented: 2026-09-17. Primary entry point: **`make up`**. Teardown:
**`make down`**. No unrecorded cluster setup, image loading, secret provisioning,
pip installation or port-forward is needed for bootstrap verification.

## Approach and choices

The user requested a single repeatable local entry point, explicit prerequisite
checks, readiness and real E2E gates, teardown, and a clean-cluster/rerun test.
The existing kind config, Docker targets, Kustomize manifests, Secret generators,
Part 3 verifier and Part 4 monitoring stack were reused.

A small Makefile delegates orchestration to `scripts/local.sh`. Bash fits the
Docker/kind/kubectl sequence, while two standard-library Python helpers handle
structured image/credential data and Kubernetes-proxied monitoring checks. No
Terraform state, Helm dependency, local registry, additional controller or host
application environment is required. `make help` documents the available targets.

Docker Compose would not exercise the required Kubernetes deployment. A script
that blindly recreates the cluster or restarts every Deployment would not be
safe or useful to rerun. Fixed mutable image tags would leave changed images
unused by existing Pods. The selected implementation instead reuses the cluster,
builds with Docker caching, and selects content-derived local image tags.

## Environmental assumptions and prerequisites

- Linux amd64/x86_64 or arm64/aarch64. The actual test machine was Linux x86_64;
  ARM64 is supported by the selected tools/images but was not exercised here.
- GNU Make, Bash, Python **3.11+**, Docker Engine with Buildx, `timeout` from
  coreutils and `flock` from util-linux. Standard Linux file utilities are assumed.
  The Docker daemon must already be running and accessible to the calling user.
- At least **2 CPUs and 4 GiB RAM** reported by Docker are enforced. Approximately
  **10 GiB free disk** is recommended for node/application/monitoring images and
  build caches. Disk availability is not measured across Docker storage backends.
- kind **v0.33.0**, matching the checked-in node image, and kubectl **1.36.x**.
  `make doctor` checks these versions and Docker access before any cluster work.
- A working local Docker context and normal registry/package-index networking.
  Initial image pulls and the container dependency build require internet access.
  This is not an air-gapped installer. Custom enterprise proxy/CA configuration
  belongs in the host/Docker setup before running it.
- Run one authorized local deployment owner from this checkout. A nonblocking
  file lock prevents overlapping `up`, `down`, `build` and `verify` operations
  here; it is not a cross-host or cross-checkout distributed lock.

The actual Docker daemon reported 22 CPUs and 30.9 GiB RAM. Host application
packages, uv, Redis, Git/GitHub access and registry credentials are not prerequisites
for `make up`. The application and E2E runner use Python 3.13 inside the images.
Only the optional `make test`/`make lint` targets require uv; they announce that uv
will synchronize the locked dependencies and managed Python when needed.

Missing prerequisites fail before cluster creation with a named error. Nothing
installs system packages, modifies Docker user permissions, or runs sudo.
Install Docker/Make/Python using the appropriate OS instructions. If only the
Kubernetes CLIs are missing, explicitly opt in to:

```bash
make tools
make up
```

`make tools` requires curl, sha256sum and install. It downloads pinned Linux
kind/kubectl release binaries, checks upstream SHA256 checksum files, and places
them in ignored `.local/bin`. Make targets use that directory automatically;
no PATH edit or system installation is necessary. Downloads have timeouts/retries.
See the official [kind quick start](https://kind.sigs.k8s.io/docs/user/quick-start/)
and [kubectl Linux installation guide](https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/).

## What `make up` does

1. Checks prerequisites, tool versions, Docker access/platform/resources and
   acquires the checkout's operation lock.
2. Creates `pokeproxy` from the pinned `infra/kind/cluster.yaml` if absent. If it
   exists, starts stopped kind node containers and reuses it. Exports a fresh
   private `.kube/kind-config`, then waits for API and node readiness.
3. Builds **both** Docker targets from the working tree with frozen runtime
   dependencies. Records Docker image IDs and adds `local-<full-image-ID>` tags.
4. Writes ignored `.local/deploy/kustomization.yaml`, referencing Git-managed
   `deploy/base` and the two exact local tags; loads both images into kind.
5. Creates application/monitoring namespaces, generates/reuses/recovers Secrets,
   and applies credentials without printing their values.
6. Applies the app base with local image selection. The declarative apply submits
   Redis/mock/proxy together; waits complete in Redis, mock, proxy order before
   verification, so a transient startup dependency gap cannot pass the gate.
7. Applies monitoring, including discovery RBAC, configuration, alerts, dashboard
   and datasource, and waits for Prometheus and Grafana readiness.
8. Runs the existing uniquely named E2E Job: authenticated Redis, two signed
   protobuf requests, exact downstream JSON receipts, cache-hit/readiness checks.
9. Uses Kubernetes Service proxies to verify healthy discovered proxy targets,
   scraped request counters from the E2E traffic, five healthy alert rules, the
   provisioned Grafana dashboard and its working Prometheus datasource. This
   requires neither host pip packages nor temporary port-forward processes.
10. Prints URLs and the helper commands that open localhost tunnels.

Every invocation of kubectl specifies this checkout's kubeconfig and the exact
`kind-pokeproxy` context. The user's default kubeconfig/current context is not
used or changed. The cluster name is fixed deliberately so teardown cannot target
an arbitrary context through an environment default.

## Idempotency and images

Unchanged Docker inputs reuse cached layers and produce the same image IDs. The
image tags in the generated overlay therefore stay the same, and `kubectl apply`
does not force Pod restarts. Changed application inputs produce new IDs/tags and
roll out normally. ConfigMap hashes similarly cause a rollout only when relevant
configuration changes. Verification Jobs are intentionally new on each run and
expire after their TTL; those short-lived Pods are excluded from idempotency's
“unchanged workload Pods” comparison.

The local tag suffix is a Docker **image/config ID**, not a registry manifest
digest. This flow never publishes it. Part 3 CI still produces SHA-based registry
tags and pins published manifest digests. Local bootstrap uses the current working
tree and does not create commits, alter `deploy/overlays/release`, or record itself
as a verified GitOps release. It refuses a cluster containing the Part 3
`pokeproxy-release-state` record: use that reconciler, or explicitly tear down the
local cluster before changing ownership. Do not run competing reconcilers.

## Credentials

Only `.secrets/kubernetes-secret.json` and `.secrets/grafana-secret.json` are
managed. The directory is private (0700); files are 0600 and ignored by Git.

- With no local or cluster copy, the existing cryptographic generators create
  fresh credentials. An empty/corrupt credential file is an error, not a cue to
  silently replace credentials.
- With matching local and cluster copies, contents are preserved.
- With a live cluster copy but no local file, bootstrap recovers a minimal Secret
  manifest privately into `.secrets`; no values are logged or passed in argv.
- If local and cluster copies disagree, bootstrap refuses to overwrite the live
  Secret. Reconcile the discrepancy deliberately. To keep the cluster's values,
  securely back up/move the conflicting local file outside its managed filename,
  then rerun `make up` to recover it. To reset this disposable environment using
  the local values, run `make down`, then `make up`. Neither route is an implicit
  credential rotation.

Symlink credential files/directories are rejected. Kubernetes access/validation
failures are reported without dumping Secret output. Existing Secret generators
remain available for manual workflows; the bootstrap adds comparison/recovery
before applying their output.

## Failure and teardown behavior

Shell failures propagate with phase, line and exit status. Image builds, image
loading, Kubernetes requests, readiness waits, E2E and monitoring checks all have
finite deadlines. Readiness retries are expected startup waits, not ignored
failures. An E2E or monitoring failure makes `make up` fail, even if Pods are Ready.
The script keeps the cluster for diagnosis and reruns; it does not claim automatic
local rollback. The separate Part 3 release flow retains its rollback semantics.

Useful diagnostics:

```bash
make status
make logs
.local/bin/kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n monitoring logs deployment/prometheus --tail=100
.local/bin/kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy -n monitoring logs deployment/grafana --tail=100
```

If using system CLIs instead of `make tools`, substitute `kubectl` for
`.local/bin/kubectl`. A damaged/partially created cluster that cannot become Ready
fails clearly; `make down` then `make up` is the explicit reset path. API failures,
image-pull failures and readiness timeout logs remain available for investigation.

`make down` deletes only kind cluster `pokeproxy` and removes this checkout's
managed kubeconfig. It is successful if the cluster is already absent. It keeps
local Secrets, downloaded CLIs, generated image selection and Docker images/cache.
It does not prune other Docker resources. Cluster data—including Redis cache,
mock receipts, Prometheus history and Grafana's ephemeral database—is deleted.

## Access and helper targets

| Command | Purpose |
| --- | --- |
| `make up` / `make down` | Full local lifecycle |
| `make doctor` / `make tools` | Diagnose prerequisites / explicitly download only kind and kubectl |
| `make build` | Build both images and write local image selection, without needing a cluster |
| `make verify` | Real-traffic E2E plus monitoring gate against the running stack |
| `make status` / `make logs` | Workload overview / follow proxy logs |
| `make grafana` | http://127.0.0.1:3000/d/pokeproxy-health (Viewer, no login) |
| `make prometheus` | http://127.0.0.1:9090 |
| `make proxy` | http://127.0.0.1:8000/ready |
| `make test` / `make lint` | Optional uv-based developer checks |

Each tunnel runs in its own terminal, binds only localhost, and stops with Ctrl-C.
The workloads keep running after the tunnel exits. Occupied local ports fail
clearly; use the documented explicit kubectl commands with another local port.

## Validation and AI-assisted flow

The assistant inspected existing deployment/verification/credential tooling,
added the Make/shell interface and standard-library helpers, tested failure
boundaries, then performed the requested real teardown/rebuild/rerun sequence.
No subagents were used. The test reused the installed Docker daemon and cached
Docker images/layers; it did not claim a pristine OS or remove unrelated resources.
Existing local credentials were deliberately retained according to `make down`'s
contract. The cluster itself and its namespaces/workloads/monitoring history were
deleted before the first bootstrap. Host `.venv` existed but was not used by
`make up`; no host application dependency install was performed.

Completed checks on 2026-09-17:

| Command/check | Result |
| --- | --- |
| `make doctor` before installing CLIs | Failed immediately with a named missing-kind prerequisite and `make tools` guidance |
| `make tools`, then `make doctor` | Both downloaded CLI checksums passed; prerequisites passed |
| `make down`, repeated while absent | Both exited 0; unrelated Docker resources and local credentials retained |
| `make down` then `make up` with finalized scripts | Exited 0; created a fresh kind cluster, built/loaded both images, deployed all five workloads and passed both gates |
| Second `make up` | Exited 0; reused cached builds/images, preserved credentials, passed both gates |
| Before/after Kubernetes metadata comparison | All five Deployment UIDs and generations, all five workload Pod UIDs, and both Secret UIDs unchanged; no unnecessary workload rollout |
| Final standalone `make verify` | Exited 0; two correlated downstream receipts verified through real protobuf/HMAC traffic, authenticated Redis/cache checks passed |
| Monitoring gate (each successful up and final verify) | One healthy PokeProxy scrape target, E2E traffic metrics, five healthy alert rules, 17 dashboard panels, working Grafana datasource |
| `make status` | All five Deployments available 1/1; five workload Pods Running with zero restarts; verification Job completed |
| `.venv/bin/pytest -q` | 114 passed, one skipped (optional host Redis TTL fixture unset; deployed Redis tested by E2E) |
| `.venv/bin/ruff check .` | Passed |
| `bash -n scripts/local.sh scripts/install-local-tools.sh` | Passed |
| `/tmp/pokeproxy-ci-tools/actionlint` | Passed |
| `git diff --check` | Passed |

The final verification Job was `pokeproxy-verify-05e299d2de74`; verified receipt
IDs were `c6c263fae8ee47caa26b254b62c2eabe` and
`b2643fa967b646a3b936ace359089eb1`. Both image IDs stayed identical across
the successful bootstrap and rerun. Verification Jobs deliberately run afresh
and produce new receipts on each invocation; they are not long-running workloads.

An earlier development attempt passed the deployment gates but returned a shell
parse error because the runner was edited while Bash was executing it. That
attempt was counted as failed. The cluster was deleted again and the complete
successful bootstrap/rerun sequence above used unchanged runner files.

The stack is left running. This validates fresh-cluster creation and reuse on
the described host; a newly provisioned OS, ARM64 and stopped-node recovery were
not separately exercised. Unit tests cover first-time credential generation,
credential recovery/preservation/mismatch rejection, mutable-image rejection,
missing prerequisite failure and monitoring failure detection.

## Follow-up: kind registry DNS failure

A later user run reached Redis rollout but failed with `ImagePullBackOff`:
containerd could not resolve `registry-1.docker.io` through the kind network DNS
server. The proxy and mock were healthy because their images were already loaded.
Bootstrap now also preloads the three digest-pinned infrastructure images using
host Docker and kind. It reuses exact pinned host references or pulls them with a
bounded timeout, then restores the canonical digest references in node containerd
because Docker image archives omit RepoDigests. The deployed manifests keep their
pinned references. Host Docker must still reach registries for uncached images;
this does not repair arbitrary external DNS access from application Pods.

Rollout failures now print Pod status and namespace events. Successful startup
prints explicit HTTP health/readiness/metrics/stats, Prometheus targets/alerts and
Grafana dashboard URLs, with their foreground port-forward commands. No background
tunnel process is started. Regression tests cover pinned-image loading and fatal
host image-pull failure.

Validation of this fix: reran `make up` against the user's failed cluster without
teardown. Redis recovered, both monitoring Deployments became ready, the two
correlated downstream receipts passed, and monitoring confirmed one healthy
scrape target, five alert rules and 17 dashboard panels. The command exited 0
and printed the expanded URL list. Full pytest: **116 passed, 1 skipped**;
Ruff, shell syntax, actionlint and `git diff --check` passed.

## Reviewer access follow-up

`make tunnels` now starts all three managed background port-forwards and checks
HTTP readiness. `make tunnels-down` stops only those processes using recorded
PID/start-time/command identity and Linux pidfds. Logs and PID records live in
`.local/tunnels/`; `make status` reports their state. `make up` remains deployment
and verification only. Foreground access commands remain available. Stop managed
tunnels explicitly before cluster teardown. See the [exact live test results](../verification/tunnels.md).
