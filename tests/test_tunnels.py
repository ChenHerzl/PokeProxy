import json
import os

from scripts import tunnels


def test_stale_pid_does_not_signal_reused_process(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnels, "STATE", tmp_path)
    path = tmp_path / "proxy.pid"
    path.write_text(json.dumps({"pid": os.getpid(), "start": "wrong", "argv": []}))
    monkeypatch.setattr(
        tunnels.os,
        "pidfd_open",
        lambda pid: (_ for _ in ()).throw(
            AssertionError("must not signal unrelated process")
        ),
    )
    tunnels.stop("proxy")
    assert not path.exists()


def test_healthy_existing_tunnel_is_reused(monkeypatch):
    monkeypatch.setattr(tunnels, "read_record", lambda name: {"pid": 123})
    monkeypatch.setattr(tunnels, "reachable", lambda *args: True)
    monkeypatch.setattr(
        tunnels.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate process")
        ),
    )
    assert tunnels.start("proxy", "kubectl") is False


def test_missing_or_corrupt_pid_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnels, "STATE", tmp_path)
    tunnels.stop("proxy")
    path = tmp_path / "proxy.pid"
    path.write_text("not json")
    tunnels.stop("proxy")
    assert not path.exists()
