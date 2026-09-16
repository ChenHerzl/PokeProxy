"""Bounded per-process metrics; use one worker per process and aggregate externally."""

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


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
        self.endpoints: dict[str, EndpointStats] = {}
        self.requests = Counter(
            "pokeproxy_requests_total",
            "Completed stream requests",
            ["outcome", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "pokeproxy_request_duration_seconds",
            "Stream duration",
            registry=self.registry,
        )
        self.forward = Counter(
            "pokeproxy_forward_total",
            "Single downstream attempts",
            ["rule", "outcome"],
            registry=self.registry,
        )
        self.forward_duration = Histogram(
            "pokeproxy_forward_duration_seconds",
            "Downstream duration",
            ["rule"],
            registry=self.registry,
        )
        self.cache = Counter(
            "pokeproxy_cache_total",
            "Cache operations",
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
