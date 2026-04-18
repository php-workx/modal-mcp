"""Rate-limit primitives for Modal MCP policy enforcement."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

GLOBAL_RATE_LIMIT_KEY = "global"


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


def rate_limit_key(
    *,
    auth_session_id: str | None = None,
    actor_principal: str | None = None,
    remote_address: str | None = None,
    mcp_session_id: str | None = None,
    method: str | None = None,
) -> str:
    """Build the rate-limit key without trusting Mcp-Session-Id alone."""

    del mcp_session_id, method
    if auth_session_id:
        return f"auth_session:{auth_session_id}"
    if actor_principal:
        return f"actor:{actor_principal}"
    if remote_address:
        return f"remote:{remote_address}"
    return GLOBAL_RATE_LIMIT_KEY


class TokenBucketRateLimiter:
    """In-memory token-bucket rate limiter with injectable time."""

    def __init__(
        self,
        *,
        capacity: float,
        refill_rate_per_second: float,
        now: Callable[[], float] | None = None,
    ) -> None:
        if capacity <= 0:
            msg = "capacity must be positive"
            raise ValueError(msg)
        if refill_rate_per_second <= 0:
            msg = "refill_rate_per_second must be positive"
            raise ValueError(msg)
        self.capacity = capacity
        self.refill_rate_per_second = refill_rate_per_second
        self._now = now or time.monotonic
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str, *, cost: float = 1.0) -> bool:
        """Consume tokens from a key bucket when capacity allows."""

        if cost <= 0:
            msg = "cost must be positive"
            raise ValueError(msg)
        current_time = self._now()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.capacity, updated_at=current_time)
            self._buckets[key] = bucket
        self._refill(bucket, current_time)
        if bucket.tokens < cost:
            return False
        bucket.tokens -= cost
        return True

    def remaining(self, key: str) -> float:
        """Return the current token count for a key after refill."""

        current_time = self._now()
        bucket = self._buckets.get(key)
        if bucket is None:
            return self.capacity
        self._refill(bucket, current_time)
        return bucket.tokens

    def _refill(self, bucket: _Bucket, current_time: float) -> None:
        elapsed = max(0.0, current_time - bucket.updated_at)
        bucket.tokens = min(
            self.capacity,
            bucket.tokens + elapsed * self.refill_rate_per_second,
        )
        bucket.updated_at = current_time


__all__ = [
    "GLOBAL_RATE_LIMIT_KEY",
    "TokenBucketRateLimiter",
    "rate_limit_key",
]
