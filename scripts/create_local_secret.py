"""Generate reusable local Kubernetes credentials without printing secret values."""

import base64
import json
import os
import secrets
from pathlib import Path


def main() -> None:
    directory = Path(".secrets")
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / "kubernetes-secret.json"
    password = secrets.token_hex(32)
    manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": "pokeproxy-credentials", "namespace": "pokeproxy"},
        "stringData": {
            "POKEPROXY_SECRET": base64.b64encode(secrets.token_bytes(32)).decode(),
            "REDIS_URL": f"redis://default:{password}@redis:6379/0",
            "REDIS_PASSWORD": password,
            # Minimal cache commands, connection setup, and authenticated probe.
            "users.acl": f"user default on >{password} ~pokeproxy:pokemon:* +get +set +ping +select +client|setinfo +client|setname\n",
        },
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"Keeping existing credentials: {path}")
        return
    with os.fdopen(descriptor, "w") as output:
        json.dump(manifest, output)
        output.write("\n")
    print(f"Created private credentials: {path}")


if __name__ == "__main__":
    main()
