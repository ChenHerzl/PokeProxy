# Deployment failure and rollback

The deployment gate is `python3 scripts/reconcile.py deploy`. An apply error,
rollout timeout, failed E2E Job, or failure to record success makes the command
exit nonzero. **A successful recovery still returns failure for the attempted
release.** CI or an operator must not interpret recovery as a successful release.

## Identifying the known-good release

After all three Deployments finish rolling out and the real-traffic Job passes,
the reconciler writes `pokeproxy/pokeproxy-release-state`. It records:

- `good_revision`: the full Git commit containing the verified desired state;
- `source_sha`: the application source revision from the CI release artifact;
- `proxy` and `mock`: the exact SHA-tagged image references and manifest digests.

These are different revisions: promotion creates a desired-state commit after
the application image has been built. The ConfigMap is observed deployment
status, not the source of desired manifests. Git remains the desired-state store.
No Secret values are included. Inspect status with:

```bash
kubectl --kubeconfig .kube/kind-config --context kind-pokeproxy \
  -n pokeproxy get configmap pokeproxy-release-state -o json
```

The reconciler must have the previous commit in its local Git object database.
Use a full clone and retain released image digests in GHCR. Before modifying the
cluster, the script resolves and validates both candidate and recovery snapshots.
Missing recovery history or invalid metadata fails before deployment begins.
An existing Part 2 deployment does not automatically count as known-good: the
first committed release must pass this gate to establish that record.

## Automatic recovery

On failure, the script records `failed_revision`, reapplies the previous verified
Git snapshot, waits for its Deployments, and runs its E2E gate again. This restores
the previous proxy/mock image digests, routing rules, settings, Redis manifest,
Services and other resources present in that snapshot. It does not rely on
`kubectl rollout undo`, which cannot restore matching ConfigMaps or Git state.

The failed revision is blocked on subsequent reconciliations. The next attempt
must use a new commit, normally a revert or fix. This prevents a local scheduled
invocation from repeatedly reintroducing the same failed desired state. There is
no automatic Git push and no continuous controller installed by this part.

The script has a local process lock, finite subprocess/rollout deadlines and a
bounded verification Job. Run exactly one authorized reconciler per cluster;
the local lock is not a distributed lock across machines. A rollout has up to
180 seconds per Deployment; verification has a 60-second Kubernetes deadline
and a 90-second polling deadline, plus bounded API/log calls. Recovery can take
the same time again. A caller should allow roughly 25 minutes for both paths.

## Making rollback durable in Git

Automatic cluster recovery deliberately leaves Git showing the failed change.
Revert the bad promotion/configuration commit through a reviewed PR, merge it,
fetch the result, then reconcile the new revision:

```bash
git fetch origin
git switch -c rollback-release origin/main  # use master if that is the default
git revert <bad-promotion-or-configuration-commit>
# Push this branch and open/merge a PR using your normal review process.
git fetch origin
python3 scripts/reconcile.py deploy --context kind-pokeproxy \
  --kubeconfig .kube/kind-config --revision origin/main
```

For several interdependent changes, restore the entire `deploy/` tree from
`good_revision` into a new commit and review the diff. Do not merely select an
old image while retaining incompatible new routing/configuration. A passing
revert becomes the new `good_revision` and clears the failure marker. No rebuild
is needed: the old immutable digests already identify the recovery artifacts.

If a bad version passes the immediate gate and fails later, there is no automatic
monitoring-driven rollback in Part 3. Revert to an earlier verified release in
Git and reconcile. The current ConfigMap keeps only the latest known-good
record; use Git history and retained successful reconciliation logs to identify
older verified releases. Keep these logs in the calling CI/job system.

## Operator intervention and limitations

- A failed first release has no known-good record; the script exits nonzero and
  leaves resources available for diagnosis. Fix/revert and deploy a new commit.
- Recovery failure, API/network outage, or inability to record status requires
  operator intervention. Do not clear the failure marker and blindly retry.
- SIGKILL, host loss or process termination can interrupt the sequence; there is
  no atomic transaction spanning Git, Kubernetes and verification. Inspect state,
  actual Pods and logs, then reconcile a reviewed recovery revision. SIGTERM
  from a killed calling job is not a guaranteed recovery mechanism.
- Secret rotation, credentials, Redis contents, mock receipt history, downstream
  side effects and external systems are not restored. Redis/receipts are
  ephemeral. A successful E2E check is not a load test or a proof of all routes.
- There is no automatic pruning. Old generated ConfigMaps and resources added
  only by a failed release remain; unused generated maps do not affect restored
  Pods. Remove obsolete objects separately after review. This avoids accidental
  namespace or Secret deletion in this lightweight implementation.

## With Argo CD

Argo CD would watch the same Git overlay and continuously reconcile it. A live
`rollout undo` or this script's cluster-only restoration would then conflict with
the still-failed desired state and could be overwritten. Do not run both writers.

Use a Git revert as the durable rollback, then let Argo reconcile it. A PostSync
verification Job can fail the sync, but does not automatically create that revert
or restore a previous release. Automated Git reverts would require an additional
controller/workflow with scoped repository credentials and explicit failure
policy. Argo documents that its rollback operation is unavailable while automated
sync is enabled; disable automated reconciliation for emergency manual recovery,
repair Git, and re-enable it afterward. See [automated sync policy](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/)
and [resource hooks](https://argo-cd.readthedocs.io/en/stable/user-guide/resource_hooks/).
