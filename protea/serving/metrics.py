"""Minimal Prometheus text-format metrics (spec §31, §38) without a client-library dependency."""

from __future__ import annotations

import threading
from collections import defaultdict

_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


def _labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"


class Metrics:
    """Counters, gauges and one latency histogram; thread-safe; rendered on demand."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._hist: dict[tuple[tuple[str, str], ...], list[int]] = defaultdict(lambda: [0] * (len(_BUCKETS) + 1))
        self._hist_sum: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)

    @staticmethod
    def _key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((labels or {}).items()))

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        with self._lock:
            self._counters[(name, self._key(labels))] += value

    def set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self._gauges[(name, self._key(labels))] = value

    def add(self, name: str, delta: float, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self._gauges[(name, self._key(labels))] += delta

    def observe(self, seconds: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(labels)
        with self._lock:
            counts = self._hist[key]
            for i, edge in enumerate(_BUCKETS):
                if seconds <= edge:
                    counts[i] += 1
            counts[-1] += 1
            self._hist_sum[key] += seconds

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, key), value in sorted(self._counters.items()):
                lines.append(f"{name}{_labels(dict(key))} {value:g}")
            for (name, key), value in sorted(self._gauges.items()):
                lines.append(f"{name}{_labels(dict(key))} {value:g}")
            for key, counts in sorted(self._hist.items()):
                base = dict(key)
                for i, edge in enumerate(_BUCKETS):
                    lines.append(
                        f"protea_request_latency_seconds_bucket{_labels({**base, 'le': str(edge)})} {counts[i]}"
                    )
                lines.append(f"protea_request_latency_seconds_bucket{_labels({**base, 'le': '+Inf'})} {counts[-1]}")
                lines.append(f"protea_request_latency_seconds_count{_labels(base)} {counts[-1]}")
                lines.append(f"protea_request_latency_seconds_sum{_labels(base)} {self._hist_sum[key]:g}")
        return "\n".join(lines) + "\n"
