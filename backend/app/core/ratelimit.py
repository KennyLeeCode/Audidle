"""Client side rate limiting.

MusicBrainz allows roughly one request per second for anonymous clients and
enforces it with 503 responses. Being throttled is not an edge case there, it is
the normal operating condition, so the limiter is part of the design rather than
a safety net.

A simple spacing limiter rather than a token bucket. Bursting is exactly what
gets a client blocked, so allowing one would be the wrong feature.
"""

import asyncio
import time


class RateLimiter:
    """Ensures a minimum interval between calls, across concurrent callers.

    The lock is what makes it correct under concurrency. Without it, ten
    coroutines would each check the clock, all see that enough time had passed,
    and fire at once.
    """

    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._min_interval = 1.0 / requests_per_second
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until the next call is allowed."""
        async with self._lock:
            elapsed = time.monotonic() - self._last_call
            wait = self._min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
