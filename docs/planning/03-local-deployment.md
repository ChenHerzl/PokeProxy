# Part 2 — containers and local Kubernetes

Completed and verified: 2026-09-17.

## Approach and decisions

Continue the hardened application with a shared multi-stage Dockerfile and
separate proxy/mock targets. The dependency stage installs the frozen runtime
lock without development dependencies or an unpinned project build. Runtime
images run as UID 10001; the Docker context allowlist excludes local credentials.
Base images are pinned by digest for reproducibility, not as a claim that they
will remain free of vulnerabilities. Updating pins and scanning images remain
delivery work.

Use kind and Kustomize for a small, inspectable local deployment. The base holds
three Deployments and ClusterIP Services; the local overlay selects locally built
images. Configuration generators add content hashes so configuration changes
update Pod templates. Routing uses Service DNS names. A single mock replica
preserves the process-local receipt oracle. Redis is an ephemeral cache with a
32 MiB eviction budget, rather than a StatefulSet with durable storage.

All containers have CPU/memory requests and limits, startup/readiness/liveness
probes, non-root users, dropped capabilities, read-only roots, RuntimeDefault
seccomp, and no service account token. The namespace enforces restricted Pod
Security. Writable temporary volumes are bounded. Proxy termination grace is
40 seconds around its 30-second server drain and bounded client cleanup.
These budgets are demonstration settings, not measured production capacity.

Generate random credentials once into an ignored, mode-0600 local file. Redis
uses an ACL restricted to cache keys and required commands; the proxy and Redis
consume a Kubernetes Secret. The generator preserves existing credentials on
reruns. Kubernetes Secrets are not an external secret manager; local cluster
administrators can read them. Rotation requires coordinated Secret replacement
and workload restarts. No secret values belong in Git or command output.

The services are private ClusterIP endpoints with optional localhost forwarding.
NetworkPolicy enforcement, TLS, HA, ingress, external secret provisioning, and
capacity testing are not implemented by this local deployment.

## Verification

The interrupted run had already built the two images and deployed the cluster.
On resumption, all three Pods were Ready. A fresh `deploy/verification` Job
completed successfully, proving Service DNS, Redis authentication/PING, two
signed protobuf requests, exact correlated JSON receipts, healthy cache readiness,
and a repeated-payload cache-hit metric. The Job has no retries and a 60-second
wall-clock deadline; a receipt mismatch fails it.

Both image configurations use `10001:10001`; their reported sizes are about
158 MB each. The regression suite passed **90 tests, with 1 skipped** in 5.74s.
The skipped case requires `TEST_REDIS_URL` for the standalone Redis TTL test;
the Kubernetes Job separately exercised the deployed Redis cache. Ruff and
`git diff --check` passed.

## AI-assisted continuation and remaining scope

The user asked to resume work interrupted by a usage limit. The assistant
inspected existing changes, the assignment, earlier decision records, and the
running cluster; reran the deployment verification and regression checks; and
added reproducible usage and this decision record. No subagents were used.
Part 3 delivery automation, Part 4 monitoring deployment, and Part 5 one-command
bootstrap are separate remaining assignment work.
