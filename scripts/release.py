"""Validate CI release metadata and update the Git-tracked release overlay."""

import argparse
import json
import re
from pathlib import Path


def validate_release(data: dict, repository: str, source_sha: str) -> dict:
    if data.get("repository", "").lower() != repository.lower():
        raise ValueError("release belongs to a different repository")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source must be a full Git SHA")
    if data.get("source_sha") != source_sha:
        raise ValueError("release source differs from successful CI run")
    prefix = f"ghcr.io/{repository.lower()}"
    for target in ("proxy", "mock"):
        expected = re.escape(f"{prefix}/{target}:sha-{source_sha}")
        if not re.fullmatch(
            expected + r"(?:-[0-9]+-[0-9]+)?@sha256:[0-9a-f]{64}", data.get(target, "")
        ):
            raise ValueError(f"invalid {target} image repository, SHA tag or digest")
    return data


def overlay(data: dict) -> dict:
    images = []
    for target, name in (("proxy", "pokeproxy"), ("mock", "pokeproxy-mock")):
        tagged, digest = data[target].split("@")
        repository, tag = tagged.rsplit(":", 1)
        images.append(
            {"name": name, "newName": repository, "newTag": tag, "digest": digest}
        )
    return {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": ["../../base"],
        "images": images,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, default=Path("deploy/overlays/release"))
    args = parser.parse_args()
    data = validate_release(
        json.loads(args.artifact.read_text()), args.repository, args.source_sha
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "kustomization.yaml").write_text(
        json.dumps(overlay(data), indent=2) + "\n"
    )
    (args.output / "release.json").write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    main()
