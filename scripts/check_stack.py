"""Check deployed monitoring through the Kubernetes API, with no host pip packages/tunnels."""

import argparse
import json
import subprocess
import time
from urllib.parse import urlencode

try:
    from .reconcile import Cluster
except ImportError:
    from reconcile import Cluster


def service_json(cluster, service, path):
    return json.loads(
        cluster.call(
            "get",
            "--raw",
            f"/api/v1/namespaces/monitoring/services/{service}:http/proxy{path}",
            timeout=25,
        )
    )


def check(cluster):
    targets = service_json(cluster, "prometheus", "/api/v1/targets")["data"][
        "activeTargets"
    ]
    proxy = [target for target in targets if target["labels"].get("job") == "pokeproxy"]
    if not proxy or any(target["health"] != "up" for target in proxy):
        raise ValueError("Waiting for healthy PokeProxy scrape targets")
    path = "/api/v1/query?" + urlencode(
        {"query": 'sum(pokeproxy_requests_received_total{job="pokeproxy"})'}
    )
    received = service_json(cluster, "prometheus", path)
    if (
        received["status"] != "success"
        or not received["data"]["result"]
        or float(received["data"]["result"][0]["value"][1]) < 2
    ):
        raise ValueError("Waiting for the E2E requests to appear in Prometheus")
    groups = service_json(cluster, "prometheus", "/api/v1/rules")["data"]["groups"]
    rules = [
        rule
        for group in groups
        if group["name"] == "pokeproxy"
        for rule in group["rules"]
    ]
    if len(rules) != 5 or any(rule["health"] != "ok" for rule in rules):
        raise ValueError("Waiting for five healthy PokeProxy alert rules")
    dashboard = service_json(
        cluster, "grafana", "/api/dashboards/uid/pokeproxy-health"
    )["dashboard"]
    if dashboard["uid"] != "pokeproxy-health" or not dashboard["panels"]:
        raise ValueError("PokeProxy dashboard has not been provisioned")
    result = service_json(
        cluster,
        "grafana",
        "/api/datasources/proxy/uid/prometheus/api/v1/query?"
        + urlencode({"query": 'up{job="pokeproxy"}'}),
    )
    if result["status"] != "success" or not result["data"]["result"]:
        raise ValueError("Grafana datasource could not query PokeProxy")
    return len(proxy), len(rules), len(dashboard["panels"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    args = parser.parse_args()
    cluster = Cluster("kind-pokeproxy", args.kubeconfig)
    deadline = time.monotonic() + 90
    while True:
        try:
            targets, rules, panels = check(cluster)
            print(
                f"PASS: {targets} healthy proxy scrape target(s), E2E metrics, {rules} alert rules, {panels} dashboard panels and Grafana datasource"
            )
            return
        except (ValueError, KeyError, subprocess.SubprocessError) as exc:
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                detail = exc.stderr.strip() or detail
            if time.monotonic() >= deadline:
                raise SystemExit(
                    f"Monitoring verification failed: {detail}. Inspect make status and monitoring logs."
                ) from None
            print(
                f"Monitoring not yet verified: {detail}; retrying in 3s",
                flush=True,
            )
            time.sleep(3)


if __name__ == "__main__":
    main()
