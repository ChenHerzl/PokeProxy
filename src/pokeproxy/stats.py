from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EndpointStats:
    request_count: int = 0
    error_count: int = 0
    total_response_time: float = 0.0
    bytes_received: int = 0
    bytes_sent: int = 0
    _response_times: list[float] = field(default_factory=list)

    def record_response_time(self, elapsed: float) -> None:
        self.total_response_time += elapsed
        bisect.insort(self._response_times, elapsed)

    @property
    def avg_response_time(self) -> float:
        return (
            self.total_response_time / self.request_count if self.request_count else 0.0
        )

    @property
    def error_rate(self) -> float:
        return self.error_count / self.request_count if self.request_count else 0.0

    def percentile(self, p: float) -> float:
        if not self._response_times:
            return 0.0
        idx = int(len(self._response_times) * p / 100)
        idx = min(idx, len(self._response_times) - 1)
        return self._response_times[idx]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 4),
            "bytes_received": self.bytes_received,
            "bytes_sent": self.bytes_sent,
        }


@dataclass
class StatsRegistry:
    endpoints: dict[str, EndpointStats] = field(default_factory=dict)

    def get(self, url: str) -> EndpointStats:
        return self.endpoints.setdefault(url, EndpointStats())

    def to_dict(self) -> dict[str, Any]:
        return {url: stats.to_dict() for url, stats in self.endpoints.items()}
