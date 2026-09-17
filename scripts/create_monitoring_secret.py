"""Generate a private Grafana admin password; local dashboards allow anonymous viewing."""

import json
import os
import secrets
from pathlib import Path


def main():
    directory = Path(".secrets")
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / "grafana-secret.json"
    manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": "grafana-admin", "namespace": "monitoring"},
        "stringData": {"password": secrets.token_urlsafe(32)},
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"Keeping existing credentials: {path}")
        return
    with os.fdopen(descriptor, "w") as output:
        json.dump(manifest, output)
        output.write("\n")
    print(f"Created private Grafana credentials: {path}")


if __name__ == "__main__":
    main()
