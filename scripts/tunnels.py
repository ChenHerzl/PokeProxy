"""Manage checkout-owned Linux kubectl tunnels without touching unrelated processes."""

import argparse
import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".local" / "tunnels"
SERVICES = {
    "proxy": ("pokeproxy", 8000, "/ready"),
    "prometheus": ("monitoring", 9090, "/-/ready"),
    "grafana": ("monitoring", 3000, "/api/health"),
}
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def identity(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return {
            "start": stat[19],
            "argv": Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1],
        }
    except (FileNotFoundError, ProcessLookupError):
        return None


def owned(record):
    current = identity(record["pid"])
    return (
        current is not None
        and current["start"] == record["start"]
        and current["argv"] == [os.fsencode(a) for a in record["argv"]]
    )


def read_record(name):
    path = STATE / f"{name}.pid"
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
        if owned(record):
            return record
    except (ValueError, KeyError, TypeError):
        pass
    path.unlink(missing_ok=True)
    return None


def stop(name):
    record = read_record(name)
    if record:
        # pidfd pins the process identity even if it exits and its PID is reused.
        try:
            fd = os.pidfd_open(record["pid"])
        except ProcessLookupError:
            fd = None
        if fd is not None:
            try:
                if owned(record):
                    signal.pidfd_send_signal(fd, signal.SIGTERM)
                    deadline = time.monotonic() + 3
                    while owned(record) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if owned(record):
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                os.close(fd)
    (STATE / f"{name}.pid").unlink(missing_ok=True)


def reachable(port, path):
    try:
        with HTTP.open(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def start(name, kubectl):
    namespace, port, path = SERVICES[name]
    record = read_record(name)
    if record and reachable(port, path):
        print(f"{name}: already running (PID {record['pid']})")
        return False
    if record:
        stop(name)
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(
                f"{name}: port {port} is occupied; leave the unrelated process alone and free the port first"
            ) from exc
    service = "pokeproxy" if name == "proxy" else name
    argv = [
        kubectl,
        "--kubeconfig",
        str(ROOT / ".kube/kind-config"),
        "--context",
        "kind-pokeproxy",
        "-n",
        namespace,
        "port-forward",
        "--address",
        "127.0.0.1",
        f"service/{service}",
        f"{port}:{port}",
        "--request-timeout=0",
    ]
    logfile = STATE / f"{name}.log"
    with logfile.open("w") as log:
        child = subprocess.Popen(  # noqa: S603 - fixed kubectl argv
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    current = identity(child.pid)
    if current is None:
        raise RuntimeError(f"{name}: port-forward exited; inspect {logfile}")
    record = {"pid": child.pid, "start": current["start"], "argv": argv}
    (STATE / f"{name}.pid").write_text(json.dumps(record))
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if child.poll() is not None:
                break
            if reachable(port, path) and owned(record):
                print(f"{name}: ready (PID {child.pid})")
                return True
            time.sleep(0.2)
        raise RuntimeError(f"{name}: did not become reachable; inspect {logfile}")
    except BaseException:
        stop(name)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "down", "status"))
    args = parser.parse_args()
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "down":
            for name in SERVICES:
                stop(name)
            print(
                "Managed tunnels stopped; PID records removed. Logs retained in .local/tunnels/."
            )
        elif args.action == "status":
            for name, (_, port, path) in SERVICES.items():
                record = read_record(name)
                state = (
                    "stopped"
                    if not record
                    else ("ready" if reachable(port, path) else "running, unreachable")
                )
                print(f"Tunnel {name}: {state} (127.0.0.1:{port})")
            print("Manage access: make tunnels / make tunnels-down")
        else:
            kubectl = shutil.which("kubectl")
            if not kubectl or not (ROOT / ".kube/kind-config").exists():
                raise RuntimeError(
                    "kubectl and checkout kubeconfig are required; run make up first"
                )
            created = []
            try:
                for name in SERVICES:
                    if start(name, kubectl):
                        created.append(name)
            except BaseException:
                for name in created:
                    stop(name)
                raise
            print(
                "\nLocal access ready (background tunnels):\n"
                "  PokeProxy  http://127.0.0.1:8000/ready\n"
                "             http://127.0.0.1:8000/health\n"
                "             http://127.0.0.1:8000/metrics\n"
                "             http://127.0.0.1:8000/stats\n"
                "  Prometheus http://127.0.0.1:9090\n"
                "             http://127.0.0.1:9090/targets\n"
                "             http://127.0.0.1:9090/alerts\n"
                "  Grafana    http://127.0.0.1:3000/d/pokeproxy-health\n"
                "Logs/PIDs: .local/tunnels/ | Stop: make tunnels-down"
            )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        raise SystemExit(f"Tunnel error: {exc}") from None
