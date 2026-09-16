"""Per-tenant request limiting for the facade (Phase 10 hardening; spec §31).

The facade is the only thing standing between a partner's runtime and the GPU, so an unbounded caller — a
retry storm, a runaway agent loop, one tenant's bulk backfill — must not be able to consume the engine for
everyone else. This is a token bucket per caller key, in-process by design:

* **In-process, per replica.** With `serve.workers: 1` (the v0 deployment) this is exact. Across replicas each
  replica admits its own share, so the effective ceiling is `rate_limit_rpm × replicas`; that is documented
  rather than hidden, and a shared limiter is a later change if more than one replica is ever run.
* **Per tenant, hashed.** The key is the same hashed `x-protea-tenant` reference the facade already uses for
  usage events (see `protea/serving/app.py:_tenant_ref`); no tenant identifier is held in the clear.
* **Fail-closed on the limit.** A caller over its rate gets `429` with `retry-after`, never a partial stream.

The bucket maths is deliberately the textbook one so the behaviour is predictable at the edge:

    tokens = min(capacity, tokens + elapsed × rpm / 60)   # refill
    admit when tokens ≥ 1, then subtract 1
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

PRUNE_AFTER_S = 3600.0  # a bucket untouched for an hour is indistinguishable from a new caller
PRUNE_AT = 10_000  # prune only when the table gets big, so the common path stays O(1)


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    """Token bucket keyed by caller. `burst` defaults to one minute's worth of requests."""

    rpm: int
    burst: int | None = None
    clock: Callable[[], float] = time.monotonic
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    @property
    def capacity(self) -> float:
        return float(self.burst or self.rpm)

    @property
    def rate_per_s(self) -> float:
        return self.rpm / 60.0

    def check(self, key: str) -> tuple[bool, float]:
        """Consume one token for `key`. Returns `(allowed, retry_after_seconds)`."""
        now = self.clock()
        if len(self._buckets) > PRUNE_AT:
            self._prune(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.capacity, updated=now)
            self._buckets[key] = bucket
        elapsed = max(0.0, now - bucket.updated)
        bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.rate_per_s)
        bucket.updated = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True, 0.0
        return False, (1.0 - bucket.tokens) / self.rate_per_s

    def _prune(self, now: float) -> None:
        stale = [k for k, b in self._buckets.items() if now - b.updated > PRUNE_AFTER_S]
        for k in stale:
            del self._buckets[k]


def build_limiter(rpm: int | None, burst: int | None = None) -> RateLimiter | None:
    """A limiter when a rate is configured, else `None` (unlimited — the caller opted out explicitly)."""
    if not rpm or rpm <= 0:
        return None
    return RateLimiter(rpm=int(rpm), burst=int(burst) if burst else None)


__all__ = ["RateLimiter", "build_limiter"]
