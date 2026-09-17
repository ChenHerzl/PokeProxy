import contextlib
import json
from pathlib import Path

import pytest

from scripts import reconcile, release

SHA = "a" * 40
DIGEST = "b" * 64


def artifact():
    return {
        "repository": "owner/repo",
        "source_sha": SHA,
        "proxy": f"ghcr.io/owner/repo/proxy:sha-{SHA}@sha256:{DIGEST}",
        "mock": f"ghcr.io/owner/repo/mock:sha-{SHA}@sha256:{DIGEST}",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha": "c" * 40},
        {"repository": "attacker/repo"},
        {"proxy": f"ghcr.io/attacker/repo/proxy:sha-{SHA}@sha256:{DIGEST}"},
        {"mock": f"ghcr.io/owner/repo/mock:latest@sha256:{DIGEST}"},
        {"proxy": f"ghcr.io/owner/repo/proxy:sha-{SHA}"},
    ],
)
def test_promotion_rejects_wrong_source_or_mutable_artifact(change):
    with pytest.raises(ValueError):
        release.validate_release(artifact() | change, "owner/repo", SHA)


def test_release_digest_cannot_diverge_from_desired_state(tmp_path):
    path = tmp_path / "deploy/overlays/release"
    path.mkdir(parents=True)
    data = artifact()
    (path / "release.json").write_text(json.dumps(data))
    manifest = release.overlay(data)
    (path / "kustomization.yaml").write_text(json.dumps(manifest))
    assert reconcile.release(tmp_path)[1] == data
    manifest["images"][0]["digest"] = "sha256:" + "c" * 64
    (path / "kustomization.yaml").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="disagree"):
        reconcile.release(tmp_path)


class FakeCluster:
    def __init__(self, *, good="previous", failure=None):
        self.saved = {"good_revision": good} if good else {}
        self.applied = []
        self.verified = []
        self.failure = failure

    def state(self):
        return self.saved.copy()

    def save(self, value):
        self.saved = value

    def apply(self, directory):
        self.applied.append(str(directory))
        if self.failure == "rollout" and str(directory) == "candidate":
            raise RuntimeError("rollout failed")

    def verify(self, image, template):
        self.verified.append(image)
        if self.failure == "e2e" and image == "candidate":
            raise RuntimeError("wrong receipt")
        if self.failure == "recovery":
            raise RuntimeError("dependency unavailable")


@pytest.fixture
def snapshots(monkeypatch):
    @contextlib.contextmanager
    def snapshot(repository, revision):
        yield revision, Path(revision)

    monkeypatch.setattr(reconcile, "snapshot", snapshot)
    monkeypatch.setattr(
        reconcile,
        "release",
        lambda root: (root, {"proxy": str(root), "mock": str(root), "source_sha": SHA}),
    )


def test_success_only_marks_revision_good_after_verification(snapshots):
    cluster = FakeCluster()
    reconcile.reconcile(cluster, Path("."), "candidate")
    assert cluster.verified == ["candidate"]
    assert cluster.saved["good_revision"] == "candidate"
    assert "failed_revision" not in cluster.saved


@pytest.mark.parametrize("failure", ["rollout", "e2e", "recovery"])
def test_failure_restores_previous_and_never_marks_candidate_good(snapshots, failure):
    cluster = FakeCluster(failure=failure)
    with pytest.raises(RuntimeError):
        reconcile.reconcile(cluster, Path("."), "candidate")
    assert cluster.applied == ["candidate", "previous"]
    assert cluster.saved["good_revision"] == "previous"
    assert cluster.saved["failed_revision"] == "candidate"
    with pytest.raises(RuntimeError, match="previously failed"):
        reconcile.reconcile(cluster, Path("."), "candidate")
    assert cluster.applied == ["candidate", "previous"]


def test_first_release_failure_has_no_invented_rollback(snapshots):
    cluster = FakeCluster(good=None, failure="e2e")
    with pytest.raises(RuntimeError):
        reconcile.reconcile(cluster, Path("."), "candidate")
    assert cluster.applied == ["candidate"]
    assert "good_revision" not in cluster.saved


@pytest.mark.parametrize(
    "status",
    [
        {"failed": 1},
        {"conditions": [{"type": "Failed", "status": "True"}]},
    ],
)
def test_e2e_job_failure_propagates_without_waiting_for_success(monkeypatch, status):
    cluster = reconcile.Cluster("test")
    job = {"spec": {"template": {"spec": {"containers": [{}]}}}}

    def call(*args, **kwargs):
        if args[0] == "create" and "--dry-run=client" in args:
            return json.dumps(job)
        if "get" in args:
            return json.dumps({"status": status})
        return ""

    monkeypatch.setattr(cluster, "call", call)
    with pytest.raises(RuntimeError, match="E2E verification failed"):
        cluster.verify("image")
