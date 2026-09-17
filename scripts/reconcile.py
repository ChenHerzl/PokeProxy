"""Reconcile a committed release, gate it with real traffic, restore last-good on failure.

Requires one authorized reconciler per cluster. Never handles Secret contents.
"""

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

try:
    from .release import overlay, validate_release
except ImportError:  # Direct script invocation as well as package imports in tests.
    from release import overlay, validate_release

ROOT = Path(__file__).resolve().parents[1]
STATE = "pokeproxy-release-state"


def run(command, *, data=None, timeout=60):
    return subprocess.run(  # noqa: S603 - argument lists, never a shell
        command, input=data, text=True, capture_output=True, check=True, timeout=timeout
    ).stdout


class Cluster:
    def __init__(self, context, kubeconfig=None):
        self.command = ["kubectl", "--context", context, "--request-timeout=15s"]
        if kubeconfig:
            self.command += ["--kubeconfig", kubeconfig]

    def call(self, *args, data=None, timeout=60):
        return run([*self.command, *args], data=data, timeout=timeout)

    def apply(self, directory):
        self.call("apply", "-k", str(directory))
        for name in ("redis", "mock-downstream", "pokeproxy"):
            print(f"Waiting for deployment/{name}", flush=True)
            self.call(
                "-n",
                "pokeproxy",
                "rollout",
                "status",
                f"deployment/{name}",
                "--timeout=180s",
                timeout=195,
            )

    def state(self):
        value = self.call(
            "-n",
            "pokeproxy",
            "get",
            "configmap",
            STATE,
            "--ignore-not-found",
            "-o",
            "json",
        )
        return json.loads(value).get("data", {}) if value.strip() else {}

    def save(self, state):
        self.call(
            "apply",
            "-f",
            "-",
            data=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": STATE, "namespace": "pokeproxy"},
                    "data": state,
                }
            ),
        )

    def verify(self, image=None, template=None):
        if image is None:
            deployment = json.loads(
                self.call(
                    "-n", "pokeproxy", "get", "deployment", "pokeproxy", "-o", "json"
                )
            )
            image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        template = template or ROOT / "deploy/verification/job.yaml"
        job = json.loads(
            self.call(
                "create",
                "--dry-run=client",
                "--validate=false",
                "-f",
                str(template),
                "-o",
                "json",
            )
        )
        name = "pokeproxy-verify-" + uuid.uuid4().hex[:12]
        job["metadata"] = {"name": name, "namespace": "pokeproxy"}
        job.pop("status", None)
        job["spec"]["template"]["spec"]["containers"][0]["image"] = image
        self.call("create", "-f", "-", data=json.dumps(job))
        print(f"Verification Job: {name}", flush=True)
        deadline = time.monotonic() + 90
        try:
            while time.monotonic() < deadline:
                status = json.loads(
                    self.call("-n", "pokeproxy", "get", "job", name, "-o", "json")
                ).get("status", {})
                if status.get("succeeded", 0):
                    return
                if status.get("failed", 0) or any(
                    c["type"] == "Failed" and c["status"] == "True"
                    for c in status.get("conditions", [])
                ):
                    raise RuntimeError(f"E2E verification failed: {name}")
                time.sleep(2)
            raise RuntimeError(f"E2E verification timed out: {name}")
        finally:
            # Keep failed Jobs for inspection; Kubernetes TTL cleans them after 10 minutes.
            try:
                print(
                    self.call(
                        "-n",
                        "pokeproxy",
                        "logs",
                        f"job/{name}",
                        "--pod-running-timeout=5s",
                    ),
                    flush=True,
                )
            except subprocess.SubprocessError:
                print(
                    f"Logs unavailable; inspect Job {name} and its Pod events.",
                    flush=True,
                )


@contextlib.contextmanager
def snapshot(repository, revision):
    commit = run(
        [
            "git",
            "-C",
            str(repository),
            "rev-parse",
            "--verify",
            f"{revision}^{{commit}}",
        ]
    ).strip()
    archive = subprocess.run(  # noqa: S603
        ["git", "-C", str(repository), "archive", commit, "deploy"],  # noqa: S607
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="pokeproxy-release-") as directory:
        with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
            contents.extractall(directory, filter="data")
        yield commit, Path(directory)


def release(root):
    directory = root / "deploy/overlays/release"
    data = json.loads((directory / "release.json").read_text())
    validate_release(data, data["repository"], data["source_sha"])
    # Reject metadata/manifests disagreement; the same digests must reach the cluster.
    if json.loads((directory / "kustomization.yaml").read_text()) != overlay(data):
        raise ValueError("release metadata and desired images disagree")
    return directory, data


def reconcile(cluster, repository, revision):
    state = cluster.state()
    with snapshot(repository, revision) as (commit, root):
        directory, data = release(root)
        if state.get("failed_revision") == commit:
            raise RuntimeError(
                "Revision previously failed; commit a Git rollback or a new fix"
            )
        previous = state.get("good_revision")
        # Resolve and validate the recovery snapshot BEFORE changing the cluster.
        with contextlib.ExitStack() as stack:
            recovery = None
            if previous:
                _, previous_root = stack.enter_context(snapshot(repository, previous))
                previous_directory, previous_data = release(previous_root)
                recovery = (previous_directory, previous_data, previous_root)
            try:
                print(f"Reconciling desired-state revision: {commit}", flush=True)
                cluster.apply(directory)
                cluster.verify(data["proxy"], root / "deploy/verification/job.yaml")
                cluster.save(
                    {
                        "good_revision": commit,
                        "source_sha": data["source_sha"],
                        "proxy": data["proxy"],
                        "mock": data["mock"],
                    }
                )
            except (Exception, KeyboardInterrupt):
                print(f"Release {commit} failed; attempting recovery.", flush=True)
                # Block a repeated reconciliation of the failed Git revision.
                failed = {**state, "failed_revision": commit}
                try:
                    cluster.save(failed)
                except subprocess.SubprocessError:
                    print(
                        "Could not record failure; stop reconciliation and repair cluster access."
                    )
                if recovery:
                    old_directory, old_data, old_root = recovery
                    cluster.apply(old_directory)
                    cluster.verify(
                        old_data["proxy"], old_root / "deploy/verification/job.yaml"
                    )
                    print(
                        f"Restored and verified {previous}; Git rollback still required.",
                        flush=True,
                    )
                else:
                    print(
                        "No verified previous revision: automatic rollback unavailable.",
                        flush=True,
                    )
                raise
    print(f"Verified known-good desired-state revision: {commit}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["verify", "deploy"])
    parser.add_argument("--context", required=True)
    parser.add_argument("--kubeconfig")
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    cluster = Cluster(args.context, args.kubeconfig)
    # One process per local kubeconfig/context; cross-host deployers are unsupported.
    identity = args.context + str(
        Path(args.kubeconfig or os.environ.get("KUBECONFIG", "~/.kube/config"))
        .expanduser()
        .resolve()
    )
    lock = Path(tempfile.gettempdir()) / (
        "pokeproxy-" + hashlib.sha256(identity.encode()).hexdigest() + ".lock"
    )
    try:
        with lock.open("w") as descriptor:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == "verify":
                cluster.verify()
            else:
                reconcile(cluster, args.repository, args.revision)
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"FAILED ({type(exc).__name__}): {exc}")
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
