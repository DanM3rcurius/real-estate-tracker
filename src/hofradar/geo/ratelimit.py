"""Politeness gate for the free upstream geo APIs.

Nominatim's usage policy caps public-instance traffic at one request per
second; OSRM's public demo server publishes no hard number but the same
courtesy applies. One limiter instance per provider, shared process-wide, so
a burst of ``locate()`` calls from the pipeline never exceeds the cap no
matter how many callers fire concurrently.
"""

from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    """Serialises ``wait()`` calls so consecutive returns are spaced apart.

    A single :class:`asyncio.Lock` makes this safe under concurrent callers:
    each coroutine blocks until it is its turn, then waits out whatever is
    left of the interval before releasing the lock to the next one.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._lock = asyncio.Lock()
        self._last_call: float | None = None

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._last_call is not None:
                remaining = self.min_interval_seconds - (now - self._last_call)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_call = time.monotonic()

    def reset(self) -> None:
        """Test hook: forget the last call time so the next ``wait()`` is free."""
        self._last_call = None


#: >= 1 second apart, per Nominatim's usage policy for the public instance.
nominatim_limiter = AsyncRateLimiter(1.0)
#: No published limit for OSRM's public demo instance, but the same courtesy applies.
osrm_limiter = AsyncRateLimiter(1.0)


def reset_all() -> None:
    """Test hook: reset every module-level limiter so tests don't pay for
    real-world sleeps just because they happen to run inside one second of
    each other."""
    nominatim_limiter.reset()
    osrm_limiter.reset()
