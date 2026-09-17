"""Prepare local-only image selection and preserve existing Kubernetes credentials."""

import argparse
import base64
import json
import os
import re
import subprocess
from pathlib import Path

try:
    from . import create_local_secret, create_monitoring_secret
    from .reconcile import Cluster
except ImportError:
    import create_local_secret
    import create_monitoring_secret
    from reconcile import Cluster


def secret_data(manifest, name, namespace):
    if (
        manifest.get("kind") != "Secret"
        or manifest.get("apiVersion") != "v1"
        or manifest.get("metadata", {}).get("name") != name
        or manifest.get("metadata", {}).get("namespace") != namespace
        or manifest.get("type") != "Opaque"
    ):
        raise ValueError(f"Invalid Secret metadata for {namespace}/{name}")
    data = manifest.get("data", {}).copy()
    for key, value in manifest.get("stringData", {}).items():
        data[key] = base64.b64encode(value.encode()).decode()
    required = (
        {"POKEPROXY_SECRET", "REDIS_URL", "REDIS_PASSWORD", "users.acl"}
        if name == "pokeproxy-credentials"
        else {"password"}
    )
    if not required.issubset(data):
        raise ValueError(f"Required keys missing in {namespace}/{name}")
    try:
        if any(not base64.b64decode(value, validate=True) for value in data.values()):
            raise ValueError
        if name == "pokeproxy-credentials":
            hmac = base64.b64decode(data["POKEPROXY_SECRET"], validate=True)
            if len(base64.b64decode(hmac, validate=True)) < 32:
                raise ValueError
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"Invalid Secret encoding/content for {namespace}/{name}"
        ) from exc
    return data


def preserve_secret(cluster, path, name, namespace, generate):
    if path.is_symlink():
        raise ValueError(f"Refusing symlink credential file: {path}")
    existing = cluster.call(
        "-n", namespace, "get", "secret", name, "--ignore-not-found", "-o", "json"
    )
    live = json.loads(existing) if existing.strip() else None
    if path.exists():
        local = json.loads(path.read_text())
        data = secret_data(local, name, namespace)
        if live and data != secret_data(live, name, namespace):
            raise ValueError(
                f"Local {path} differs from cluster Secret {namespace}/{name}; "
                "refusing credential rotation. See docs/planning/06-automation.md#credentials"
            )
        path.chmod(0o600)
        print(f"Preserving credentials: {namespace}/{name}")
    elif live:
        data = secret_data(live, name, namespace)
        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {"name": name, "namespace": namespace},
            "data": data,
        }
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(manifest, output)
            output.write("\n")
        print(f"Recovered existing credentials into {path} (values not printed)")
    else:
        generate()
        secret_data(json.loads(path.read_text()), name, namespace)


def image_overlay(proxy, mock, destination):
    for name, image in (("pokeproxy", proxy), ("pokeproxy-mock", mock)):
        if not re.fullmatch(re.escape(name) + r":local-[0-9a-f]{64}", image):
            raise ValueError("Local image must be tagged with its full Docker image ID")
    manifest = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": ["../../deploy/base"],
        "images": [
            {"name": image.split(":", 1)[0], "newTag": image.split(":", 1)[1]}
            for image in (proxy, mock)
        ],
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "kustomization.yaml").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(f"Local image selection written to {destination / 'kustomization.yaml'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    images = commands.add_parser("images")
    images.add_argument("proxy")
    images.add_argument("mock")
    secrets = commands.add_parser("secrets")
    secrets.add_argument("--kubeconfig", required=True)
    args = parser.parse_args()
    try:
        if args.command == "images":
            image_overlay(args.proxy, args.mock, Path(".local/deploy"))
        else:
            directory = Path(".secrets")
            if directory.is_symlink():
                raise ValueError("Refusing symlink .secrets directory")
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
            cluster = Cluster("kind-pokeproxy", args.kubeconfig)
            preserve_secret(
                cluster,
                directory / "kubernetes-secret.json",
                "pokeproxy-credentials",
                "pokeproxy",
                create_local_secret.main,
            )
            preserve_secret(
                cluster,
                directory / "grafana-secret.json",
                "grafana-admin",
                "monitoring",
                create_monitoring_secret.main,
            )
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ) as exc:
        # Kubernetes errors can contain validation inputs. Never print Secret output/stderr.
        if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
            print(f"ERROR: {exc}")
        else:
            print(
                f"ERROR: Local configuration failed ({type(exc).__name__}); check credential files and cluster access."
            )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
