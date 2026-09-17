"""Prove discovery, traffic metrics, alert loading and Grafana datasource/dashboard access."""

import argparse
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

try:
    from .reconcile import ROOT, Cluster
except ImportError:
    from reconcile import ROOT, Cluster


QUERIES = {
    "received": 'sum(pokeproxy_requests_received_total{job="pokeproxy"})',
    "finished": 'sum(pokeproxy_requests_total{job="pokeproxy"})',
    "forwarded": 'sum(pokeproxy_requests_total{job="pokeproxy",outcome="forwarded"})',
    "unmatched": 'sum(pokeproxy_requests_total{job="pokeproxy",outcome="unmatched"})',
    "invalid_hmac": 'sum(pokeproxy_requests_total{job="pokeproxy",outcome="invalid_signature"})',
    "rule_matches": 'sum(pokeproxy_rule_matches_total{job="pokeproxy"})',
    "forward_success": 'sum(pokeproxy_forward_total{job="pokeproxy",outcome="success"})',
    "cache_hits": 'sum(pokeproxy_cache_total{job="pokeproxy",outcome="hit"})',
    "cache_misses": 'sum(pokeproxy_cache_total{job="pokeproxy",outcome="miss"})',
    "cache_errors": 'sum(pokeproxy_cache_total{job="pokeproxy",outcome=~"read_error|write_error"})',
    "handler_latency_count": 'sum(pokeproxy_request_duration_seconds_count{job="pokeproxy"})',
    "forward_latency_count": 'sum(pokeproxy_forward_duration_seconds_count{job="pokeproxy"})',
}


def get(client, url, **kwargs):
    response = client.get(url, **kwargs)
    response.raise_for_status()
    return response.json()


def query(client, url, expression, at=None):
    params = {"query": expression}
    if at is not None:
        params["time"] = str(at)
    response = get(client, url + "/api/v1/query", params=params)
    if response["status"] != "success":
        raise RuntimeError("Prometheus query failed")
    return response["data"]["result"]


def samples(client, url):
    # One evaluation instant avoids mixing scrapes across several HTTP queries.
    at = time.time()
    return {
        name: sum(float(row["value"][1]) for row in query(client, url, expr, at))
        for name, expr in QUERIES.items()
    }


def process_identity(client, url):
    return sorted(
        (row["metric"]["instance"], row["value"][1])
        for row in query(client, url, 'process_start_time_seconds{job="pokeproxy"}')
    )


def run_traffic(cluster):
    deployed = json.loads(
        cluster.call("-n", "pokeproxy", "get", "deployment", "pokeproxy", "-o", "json")
    )
    image = deployed["spec"]["template"]["spec"]["containers"][0]["image"]
    template = ROOT / "deploy/monitoring/traffic/job.yaml"
    job = json.loads(
        cluster.call(
            "create",
            "--dry-run=client",
            "--validate=false",
            "-f",
            str(template),
            "-o",
            "json",
        )
    )
    name = "pokeproxy-traffic-" + uuid.uuid4().hex[:12]
    job["metadata"] = {"name": name, "namespace": "pokeproxy"}
    job.pop("status", None)
    job["spec"]["template"]["spec"]["containers"][0]["image"] = image
    cluster.call("create", "-f", "-", data=json.dumps(job))
    print(f"Running synthetic traffic Job {name}", flush=True)
    deadline = time.monotonic() + 110
    try:
        while time.monotonic() < deadline:
            state = json.loads(
                cluster.call("-n", "pokeproxy", "get", "job", name, "-o", "json")
            ).get("status", {})
            if state.get("succeeded"):
                return
            if state.get("failed") or any(
                c["type"] == "Failed" and c["status"] == "True"
                for c in state.get("conditions", [])
            ):
                raise RuntimeError("Synthetic traffic Job failed")
            time.sleep(2)
        raise RuntimeError("Synthetic traffic Job did not complete before deadline")
    finally:
        print(
            cluster.call(
                "-n", "pokeproxy", "logs", "job/" + name, "--pod-running-timeout=5s"
            ),
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--kubeconfig")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--grafana-url", default="http://127.0.0.1:3000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    prometheus, grafana = args.prometheus_url.rstrip("/"), args.grafana_url.rstrip("/")
    cluster = Cluster(args.context, args.kubeconfig)
    with httpx.Client(timeout=5, trust_env=False) as client:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            up = query(client, prometheus, 'up{job="pokeproxy"}')
            if (
                up
                and all(float(row["value"][1]) == 1 for row in up)
                and process_identity(client, prometheus)
            ):
                break
            time.sleep(2)
        else:
            raise RuntimeError("No healthy discovered proxy metrics within 60 seconds")
        before = samples(client, prometheus)
        identity = process_identity(client, prometheus)
        run_traffic(cluster)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            after = samples(client, prometheus)
            delta = {name: after[name] - before[name] for name in before}
            required = (
                "received",
                "finished",
                "forwarded",
                "unmatched",
                "invalid_hmac",
                "rule_matches",
                "forward_success",
                "cache_hits",
                "handler_latency_count",
                "forward_latency_count",
            )
            if all(delta[name] > 0 for name in required):
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"Traffic metrics did not appear: {delta}")
        if identity != process_identity(client, prometheus):
            raise RuntimeError(
                "Proxy processes changed during measurement; rerun without a rollout"
            )
        if delta["cache_errors"] != 0:
            raise RuntimeError("Unexpected cache errors during demonstration")

        groups = get(client, prometheus + "/api/v1/rules")["data"]["groups"]
        rules = [
            rule
            for group in groups
            if group["name"] == "pokeproxy"
            for rule in group["rules"]
        ]
        if len(rules) != 5 or any(rule["health"] != "ok" for rule in rules):
            raise RuntimeError("Expected five healthy alert rules")
        get(client, grafana + "/api/health")
        dashboard = get(client, grafana + "/api/dashboards/uid/pokeproxy-health")[
            "dashboard"
        ]
        # Check every dashboard expression with real Prometheus data; idle series may be empty.
        queries = 0
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                query(
                    client, prometheus, target["expr"].replace("$__rate_interval", "1m")
                )
                queries += 1
        proxy = get(
            client,
            grafana + "/api/datasources/proxy/uid/prometheus/api/v1/query",
            params={"query": 'up{job="pokeproxy"}'},
        )
        if proxy["status"] != "success" or not proxy["data"]["result"]:
            raise RuntimeError("Grafana datasource could not query the proxy metrics")
        result = {
            "verified_at": datetime.now(UTC).isoformat(),
            "proxy_targets": len(up),
            "dashboard_uid": dashboard["uid"],
            "dashboard_queries_checked": queries,
            "healthy_alert_rules": len(rules),
            "before": before,
            "after": after,
            "delta": delta,
        }
        text = json.dumps(result, indent=2) + "\n"
        print(text)
        if args.output:
            args.output.write_text(text)


if __name__ == "__main__":
    main()
