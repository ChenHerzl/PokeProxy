import base64
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import create_local_secret, create_monitoring_secret
from scripts.check_stack import check
from scripts.local_config import image_overlay, preserve_secret


def credentials():
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": "grafana-admin", "namespace": "monitoring"},
        "data": {"password": base64.b64encode(b"local-test-only-password").decode()},
    }


def unexpected_generation():
    pytest.fail("Existing credentials must not be regenerated")


@pytest.mark.parametrize(
    ("filename", "name", "namespace", "generate"),
    [
        (
            "kubernetes-secret.json",
            "pokeproxy-credentials",
            "pokeproxy",
            create_local_secret.main,
        ),
        (
            "grafana-secret.json",
            "grafana-admin",
            "monitoring",
            create_monitoring_secret.main,
        ),
    ],
)
def test_first_boot_generates_private_credentials(
    tmp_path, monkeypatch, filename, name, namespace, generate
):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".secrets"
    directory.mkdir(mode=0o700)
    path = directory / filename
    preserve_secret(
        SimpleNamespace(call=lambda *args: ""), path, name, namespace, generate
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["metadata"] == {
        "name": name,
        "namespace": namespace,
    }


def test_missing_local_credentials_are_recovered_privately(tmp_path, capsys):
    manifest = credentials()
    cluster = SimpleNamespace(call=lambda *args: json.dumps(manifest))
    path = tmp_path / "secret.json"
    preserve_secret(cluster, path, "grafana-admin", "monitoring", unexpected_generation)
    assert json.loads(path.read_text()) == manifest
    assert path.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr().out
    assert manifest["data"]["password"] not in output
    assert "local-test-only-password" not in output


def test_matching_credentials_are_preserved_and_mismatch_refused(tmp_path):
    manifest = credentials()
    path = tmp_path / "secret.json"
    path.write_text(json.dumps(manifest))
    original = path.read_bytes()
    cluster = SimpleNamespace(call=lambda *args: json.dumps(manifest))
    preserve_secret(cluster, path, "grafana-admin", "monitoring", unexpected_generation)
    assert path.read_bytes() == original
    manifest["data"]["password"] = base64.b64encode(b"changed-secret").decode()
    with pytest.raises(ValueError, match="refusing credential rotation"):
        preserve_secret(
            cluster, path, "grafana-admin", "monitoring", unexpected_generation
        )
    assert path.read_bytes() == original


def test_local_images_cannot_use_mutable_tags(tmp_path):
    with pytest.raises(ValueError, match="full Docker image ID"):
        image_overlay("pokeproxy:latest", "pokeproxy-mock:latest", tmp_path)
    assert not list(tmp_path.iterdir())


def test_missing_kind_fails_before_docker_or_cluster_mutation():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(  # noqa: S603
        ["bash", str(root / "scripts/local.sh"), "up"],  # noqa: S607
        env={**os.environ, "KIND_BIN": "nonexistent-pokeproxy-test-kind"},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Missing prerequisite 'nonexistent-pokeproxy-test-kind'" in result.stderr
    assert "Create or start" not in result.stdout


def test_monitoring_gate_rejects_missing_application_metrics():
    def call(*args, **kwargs):
        path = args[-1]
        if path.endswith("/targets"):
            return json.dumps(
                {
                    "data": {
                        "activeTargets": [
                            {"labels": {"job": "pokeproxy"}, "health": "up"}
                        ]
                    }
                }
            )
        return json.dumps({"status": "success", "data": {"result": []}})

    with pytest.raises(ValueError, match="E2E requests"):
        check(SimpleNamespace(call=call))


@pytest.mark.parametrize("pull_fails", [False, True])
def test_dependency_preload_preserves_digest_and_propagates_pull_failure(
    monkeypatch, pull_fails
):
    import runpy

    module = runpy.run_path("scripts/load-dependency-images.py", run_name="test_loader")
    main = module["main"]
    image = "redis:7.4.8-alpine@sha256:" + "a" * 64
    calls = []
    inspections = 0

    def fake_call(*args, **kwargs):
        nonlocal inspections
        calls.append(args)
        if args[1:3] == ("get", "nodes"):
            return "pokeproxy-control-plane"
        if args[:3] == ("docker", "image", "inspect"):
            inspections += 1
            if inspections == 1:
                raise subprocess.CalledProcessError(1, args)
            return "sha256:" + "b" * 64
        if args[:2] == ("docker", "pull") and pull_fails:
            raise subprocess.CalledProcessError(1, args, stderr="registry unavailable")
        return ""

    monkeypatch.setitem(main.__globals__, "call", fake_call)
    monkeypatch.setitem(main.__globals__, "images", lambda: iter([image]))
    if pull_fails:
        with pytest.raises(subprocess.CalledProcessError):
            main()
        assert not any(c[:2] == ("docker", "exec") for c in calls)
    else:
        main()
        assert ("docker", "pull", image) in calls
        assert calls[-1][-1] == "docker.io/library/redis@sha256:" + "a" * 64
        assert "--force" in calls[-1]
