"""Legacy in-memory sliding-window rate limiter compatibility shim."""
from __future__ import annotations

from collections import defaultdict
from threading import RLock
import math
import time


class RateLimiter:
    """Simple sliding-window limiter used by the legacy Flask routes/tests."""

    def __init__(self, default_api_limit=(100, 60), cleanup_interval=60):
        self.default_api_limit = default_api_limit
        self._cleanup_interval = cleanup_interval
        self._hits = defaultdict(list)
        self._route_limits = {}
        self._exempt_prefixes = ('/static/', '/favicon.ico')
        self._lock = RLock()
        self._last_cleanup = time.time()

    def set_route_limit(self, route, max_requests, window_seconds):
        self._route_limits[route] = (max_requests, window_seconds)

    def _cleanup_old_entries(self):
        """Purge timestamps older than the fixed memory-retention cutoff."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        cutoff = now - 300
        for key, hits in list(self._hits.items()):
            pruned = [ts for ts in hits if ts > cutoff]
            if pruned:
                self._hits[key] = pruned
            else:
                del self._hits[key]

        self._last_cleanup = now

    def _get_retry_after(self, key, window_seconds):
        """Return seconds until the oldest hit falls out of the window."""
        hits = self._hits.get(key)
        if not hits:
            return 1

        oldest = min(hits)
        remaining = math.ceil((oldest + window_seconds) - time.time())
        return max(1, int(remaining))

    def is_rate_limited(self, key, max_requests, window_seconds):
        with self._lock:
            now = time.time()
            self._cleanup_old_entries()
            cutoff = now - window_seconds
            hits = [ts for ts in self._hits[key] if ts > cutoff]
            self._hits[key] = hits
            if len(hits) >= max_requests:
                return True
            hits.append(now)
            return False


limiter = RateLimiter()
