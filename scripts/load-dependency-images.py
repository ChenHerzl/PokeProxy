"""Load digest-pinned infrastructure images through host Docker into kind."""

import os
import re
import subprocess
from pathlib import Path


def call(*args, timeout=300):
    return subprocess.run(  # noqa: S603 - fixed executables and validated manifests
        args,
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout,
    ).stdout.strip()


def images():
    for path in (
        "deploy/base/redis.yaml",
        "deploy/monitoring/prometheus.yaml",
        "deploy/monitoring/grafana.yaml",
    ):
        matches = re.findall(r"^\s+image:\s*(\S+)\s*$", Path(path).read_text(), re.M)
        if len(matches) != 1 or not re.fullmatch(
            r"[^\s@]+@sha256:[a-f0-9]{64}", matches[0]
        ):
            raise ValueError(f"Expected one digest-pinned image in {path}")
        yield matches[0]


def main():
    kind = os.environ.get("KIND_BIN", "kind")
    nodes = call(kind, "get", "nodes", "--name", "pokeproxy", timeout=30).splitlines()
    if not nodes:
        raise ValueError("No pokeproxy kind nodes found")
    for image in images():
        print(f"Preparing pinned dependency: {image}", flush=True)
        # Reuse only the exact pinned reference; a mutable version tag is insufficient.
        try:
            identity = call(
                "docker", "image", "inspect", image, "--format", "{{.Id}}", timeout=20
            )
        except subprocess.CalledProcessError:
            print("Downloading through host Docker (up to 300s)...", flush=True)
            call("docker", "pull", image)
            identity = call(
                "docker", "image", "inspect", image, "--format", "{{.Id}}", timeout=20
            )
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
            raise ValueError("Invalid Docker image identity")
        alias = f"pokeproxy-dependency:local-{identity[7:]}"
        call("docker", "tag", image, alias, timeout=20)
        print(
            call(kind, "load", "docker-image", "--name", "pokeproxy", alias), flush=True
        )
        repository, digest = image.split("@")
        repository = repository.rsplit(":", 1)[0]
        if "/" not in repository:
            repository = "docker.io/library/" + repository
        elif "." not in repository.split("/")[0]:
            repository = "docker.io/" + repository
        # Docker archives omit RepoDigests. Restore the exact reference requested by
        # Kubernetes, using only an alias imported from the verified pinned image.
        for node in nodes:
            call(
                "docker",
                "exec",
                node,
                "ctr",
                "-n",
                "k8s.io",
                "images",
                "tag",
                "--force",
                "docker.io/library/" + alias,
                repository + "@" + digest,
                timeout=30,
            )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        detail = (
            exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        )
        raise SystemExit(f"Dependency image preload failed: {detail}") from None
