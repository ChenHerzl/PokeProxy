"""Bounded per-process metrics; use one worker per process and aggregate externally."""

from dataclasses import dataclass

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
)


@dataclass
class EndpointStats:
    request_count: int = 0
    error_count: int = 0
    total_response_time: float = 0.0
    bytes_received: int = 0
    bytes_sent: int = 0

    def to_dict(self) -> dict:
        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "error_rate": self.error_count / self.request_count
            if self.request_count
            else 0,
            "bytes_received": self.bytes_received,
            "bytes_sent": self.bytes_sent,
            "avg_response_time": self.total_response_time / self.request_count
            if self.request_count
            else 0,
        }


class StatsRegistry:
    def __init__(self):
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        self.endpoints: dict[str, EndpointStats] = {}
        self.received = Counter(
            "pokeproxy_requests_received_total",
            "POST /stream handler entries, before admission or input validation",
            registry=self.registry,
        )
        self.requests = Counter(
            "pokeproxy_requests_total",
            "Finished POST /stream handlers, including rejections and cancellations",
            ["outcome", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "pokeproxy_request_duration_seconds",
            "POST /stream handler time including upload, cache, routing and forwarding; excludes response transmission",
            buckets=(
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1,
                2.5,
                5,
                10,
                15,
                20,
                30,
            ),
            registry=self.registry,
        )
        self.rule_matches = Counter(
            "pokeproxy_rule_matches_total",
            "First-match routing decisions, before downstream forwarding",
            ["rule"],
            registry=self.registry,
        )
        self.forward = Counter(
            "pokeproxy_forward_total",
            "Finished single downstream attempts; success is an HTTP status below 400",
            ["rule", "outcome"],
            registry=self.registry,
        )
        self.forward_duration = Histogram(
            "pokeproxy_forward_duration_seconds",
            "Downstream attempt time through bounded raw response-body read, including failed attempts",
            ["rule"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15),
            registry=self.registry,
        )
        self.cache = Counter(
            "pokeproxy_cache_total",
            "Cache operation results: hit/miss/read_error for reads; write/write_error for writes",
            ["outcome"],
            registry=self.registry,
        )
        self.inflight = Gauge(
            "pokeproxy_inflight", "Admitted stream requests", registry=self.registry
        )

    def get(self, rule_id: str) -> EndpointStats:
        return self.endpoints.setdefault(rule_id, EndpointStats())

    def to_dict(self) -> dict:
        return {rule: value.to_dict() for rule, value in self.endpoints.items()}
