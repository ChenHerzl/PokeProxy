# Part 3 — CI/CD and GitOps

Implemented and locally exercised: 2026-09-17.

## Plan and boundaries

The user requested GitHub Actions CI, immutable artifacts, Git-owned desired
state, separate promotion and reconciliation, an actual deployment E2E gate,
and explicit rollback behavior. The existing Part 2 Kustomize base, multi-stage
Dockerfile and deterministic protobuf verifier were reused.

Choose a small manual-triggered promotion workflow and local pull-style
reconciler. GitHub-hosted runners cannot reach the laptop's private kind API.
Installing Argo CD adds controllers, CRDs, repository access and hook/recovery
configuration without improving the single-cluster demonstration enough to
justify that setup here. No Argo installation or continuous drift correction is
claimed. The desired-state path and immutable images remain directly usable by
Argo later. A self-hosted Actions runner with cluster-admin access was rejected
as unnecessary for this assignment, especially for pull-request execution.

## CI: produce artifacts

`.github/workflows/ci.yml` runs for pull requests, pushes to `main`/`master`, and
manual dispatch. If your default branch has another name, update that push list.

1. Check out the source without persisted Git credentials.
2. Configure Python 3.13.12 and uv 0.10.9; install `uv.lock` with `--frozen`.
3. Run Ruff, then all pytest cases including real Redis TTL and socket/signal tests.
4. Build the Python wheel and source distribution with `uv build` and upload them.
5. Render and strictly validate all four Kubernetes configurations; run actionlint
   on both workflows. Validation occurs before expensive image builds.
6. Build the proxy and mock Docker targets on linux/amd64. Only a default-branch
   **push** publishes GHCR images; PR/manual runs build without publishing.
7. Upload a `release` artifact containing source SHA, repository and both exact
   image references/digests after publishing succeeds.

Tags are `sha-<full-git-sha>-<run-id>-<run-attempt>`. The run/attempt suffix means
even a workflow rerun cannot overwrite its earlier artifact. There is no `latest`
tag. Desired state also pins `@sha256:<digest>`: registry tags are technically
mutable, so the digest is the enforcement of immutable content. The Python
release validator also accepts a plain `sha-<full-sha>` tag for local builds.

The application container installs the frozen runtime lock. The separate Python
distribution build uses the project's existing Hatchling build backend; its
build dependencies are not fully locked, so bit-for-bit reproducible sdists are
not claimed. Container base images and GitHub Actions are pinned by digest or
commit; tool installers pin versions and verify upstream release checksums.

uv's download cache is keyed by the lock; it avoids repeated dependency downloads.
BuildKit's GitHub Actions cache has separate proxy/mock scopes and preserves
dependency layers. The Dockerfile copies dependency metadata before application
source, so typical code-only changes reuse dependency installation. No kubeconfig
or Secret is cached or uploaded. See [Docker's cache guidance](https://docs.docker.com/build/ci/github-actions/cache/).

## CD: update desired state

`.github/workflows/promote.yml` is a separate manually dispatched workflow. Supply
the successful CI run ID. It verifies that the run succeeded, came from a push
to this repository's default branch, and used `.github/workflows/ci.yml`. It
downloads only the named release artifact, validates its source SHA, registry
repository, tag and digests, then proposes a PR updating:

- `deploy/overlays/release/release.json`: release provenance and image references;
- `deploy/overlays/release/kustomization.yaml`: actual Git-owned desired images.

In GitHub's Actions UI, select **Promote release desired state**, choose the
default branch, and supply the successful CI run ID. With authenticated `gh`,
the equivalent is `gh workflow run promote.yml -f ci_run_id="$CI_RUN_ID"` after
setting `CI_RUN_ID` to that run's numeric ID. Review the generated PR before merge.

The generated Kustomization uses JSON syntax, which is valid YAML and readable
by kubectl/Kustomize. It reuses `deploy/base`. Promotion performs no rebuild and
has no cluster credentials. Reviewing/merging the PR promotes desired state;
it is not yet proof of a successful deployment. The initial checked-in overlay
is explicitly unconfigured: the reconciler refuses it until a real validated
release artifact has been promoted and committed. No fictitious GHCR digest is
presented as a deployable release.

The workflow uses the ephemeral `GITHUB_TOKEN`, with only contents/PR write and
Actions read permissions. CI publishes with packages write. There are no embedded
credentials or personal access tokens. Enable repository Actions permissions to
create PRs. Bot PRs created with `GITHUB_TOKEN` do not trigger other workflows:
run CI manually on the promotion branch or have a maintainer close/reopen the PR
if checks are required. A GitHub App token would remove that interaction if
fully automatic promotion were later needed. See [GitHub's trigger rules](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-when-your-workflow-runs/triggering-a-workflow)
and [GHCR publishing guidance](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).

Make GHCR packages public for this local demo, or provision a namespace-scoped
registry pull Secret outside Git and configure it on the namespace's default
ServiceAccount (used by both Deployments and verification Jobs). Do not commit
registry credentials. Keep the existing `pokeproxy-credentials` Secret provisioned
as described in Part 2. Retain previous released digests for rollback.

## Cluster reconciliation and gate

From a trusted full clone on the machine that can access kind:

```bash
git fetch origin
python3 scripts/reconcile.py deploy --context kind-pokeproxy \
  --kubeconfig .kube/kind-config --revision origin/main
```

Use `origin/master` for a master-based repository. The script extracts `deploy/`
from that committed revision into a temporary directory; uncommitted files do
not become desired state. It verifies agreement between release metadata and the
overlay, preloads the previous known-good Git snapshot, applies manifests, waits
for all three rollouts, and runs a uniquely named verification Job. Only a passing
Job permits recording the new known-good release. Use one authorized reconciler
per cluster; no continuous daemon or distributed deployment lock is supplied.

Standalone gate, usable from any CI runner with trusted cluster access:

```bash
bash scripts/e2e-verify.sh --context kind-pokeproxy \
  --kubeconfig .kube/kind-config
```

The script derives the deployed proxy image for its test runner. In reconciliation,
the runner uses the exact promoted proxy digest and the Job template from that Git
revision. Kubernetes supplies credentials through Secret references; the host
does not read them. Inside the cluster the Job:

1. Authenticates to Redis over Service DNS and checks PING.
2. Sends two real HMAC-signed Charizard protobuf requests through `/stream`.
3. Queries the mock's correlated receipts and compares every JSON field, reason,
   request ID and exact receipt count. HTTP 200 alone cannot pass.
4. Checks cache readiness and the repeated-payload cache-hit metric.

The Job has no retries and a 60-second deadline. The wrapper detects failure or
timeout, prints logs and exits nonzero. It leaves uniquely named Jobs for diagnosis;
Kubernetes TTL removes finished Jobs after ten minutes. Calls and local polling
also have finite deadlines. No mock receipts are cleared by verification.

On failure, the reconciler restores and re-verifies the previous Git snapshot but
still exits nonzero, marking the candidate failed. A Git revert is required to
align desired state. See [the complete rollback procedure](../rollback.md).

## Where Argo CD would fit

Replace `scripts/reconcile.py deploy` with one Argo Application: repository URL
of this project, `targetRevision` set to the desired-state branch,
`path: deploy/overlays/release`, destination `https://kubernetes.default.svc`,
namespace `pokeproxy`. Provision repository/registry credentials outside Git.
Promotion remains unchanged: the merged overlay is the input to reconciliation.

Add the verification Job to an Argo-specific overlay as a `PostSync` hook, with
`argocd.argoproj.io/hook: PostSync` and an appropriate hook deletion policy.
Rewrite its proxy image with the same release digest. Argo waits for Deployment
health before PostSync and the Job's nonzero exit fails the sync. The current
base verification Job is deliberately separate from ordinary workload apply;
applying an ordinary completed Job would not rerun the gate on each release.

Do not run Argo and the local reconciler as competing writers. A failed PostSync
does not automatically restore old Git desired state. Implement a reviewed Git
revert, or a separate revert automation, as explained in the rollback document.

## Local validation and AI-assisted flow

The assistant inspected the existing source/build/verification structure, checked
official GitHub/Docker/Argo documentation, implemented the three separated flows,
and exercised both positive and negative deployment paths. No subagents were used.
No remote repository, GHCR credentials or hosted Actions run was configured in
this workspace; remote image publication and PR creation are defined and runnable,
but not claimed executed.

Commands/results from this implementation session:

| Check | Result |
| --- | --- |
| `.venv/bin/ruff check .` | Passed |
| `TEST_REDIS_URL=redis://127.0.0.1:32768/0 .venv/bin/pytest -q` | 104 passed, no skips, 6.89s |
| `uv lock --check --offline` using uv 0.10.9 | Passed, 36 packages |
| `uv build` | Built wheel and source distribution |
| `docker build --target proxy` and `--target mock`, full SHA tags | Both passed; dependency layers cached |
| actionlint 1.7.7 on both workflows | Passed |
| `bash -n` on all new shell scripts | Passed |
| `bash scripts/validate-kubernetes.sh` with kubeconform 0.7.0 | 34 rendered resources valid, zero invalid/errors/skips |
| `bash scripts/e2e-verify.sh --context kind-pokeproxy --kubeconfig .kube/kind-config` | Passed against deployed services |
| Temporary Git release, actual kind reconciliation | Rollouts and E2E passed; recorded known-good revision |
| Git commit changing routing reason to an incorrect value | Real receipt mismatch failed gate; restored previous configuration and re-verified; process exited 1 |
| Retry identical failed Git revision | Refused before apply, exit 1 |
| Commit a Git revert and reconcile it | Passed; recorded new good revision and cleared failure marker |
| Promotion CLI with SHA/run/attempt tags | Generated overlay preserved both tags and exact digests; Kustomize rendered it successfully |
| Final delivery-only regression run after tag-format update | 13 passed |

The local demonstration used a separate `/tmp/pokeproxy-delivery-demo` Git repo
and locally loaded manifest-digest aliases; nothing was pushed to GHCR. It tested
configuration rollback with the same application images. Unit tests additionally
exercise rollout failure, changing image references, recovery failure, absent
first-release history, mismatched artifact provenance and digest disagreement.
Temporary deployment state is removed and the original local overlay restored
after testing. No performance, HA or monitoring-driven recovery is claimed.
