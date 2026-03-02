"""
Simple in-memory rate limiter for GitHub Dossier.

Uses a sliding window counter approach. Thread-safe. No external dependencies.
Suitable for single-process Replit deployments.

Usage:
    from rate_limiter import limiter, rate_limit

    # As a decorator
    @app.route('/api/something', methods=['POST'])
    @rate_limit('10/minute')
    def my_route():
        ...

    # Register global limits via before_request
    limiter.init_app(app)
    limiter.set_route_limit('/login', 10, 60)
"""
import functools
import logging
import math
import time
from collections import defaultdict
from threading import Lock

from flask import request, jsonify


class RateLimiter:
    """Thread-safe sliding window rate limiter with automatic memory cleanup."""

    def __init__(self):
        # {key: [timestamp, ...]}
        self._hits = defaultdict(list)
        self._lock = Lock()
        self._last_cleanup = time.time()
        self._cleanup_interval = 60  # seconds between cleanups

        # Default limits (can be overridden)
        self.default_api_limit = (100, 60)      # 100 requests per 60 seconds

        # Route-specific overrides: path_prefix -> (max_requests, window_seconds)
        self._route_limits = {}

        # Exempt prefixes (no rate limiting at all)
        self._exempt_prefixes = ('/static/', '/favicon.ico')

    def init_app(self, app):
        """Register the rate limiter as a before_request hook."""
        app.before_request(self._check_rate_limit)

    def set_route_limit(self, prefix: str, max_requests: int, window_seconds: int):
        """Set a custom rate limit for routes matching a prefix."""
        self._route_limits[prefix] = (max_requests, window_seconds)

    def _get_client_key(self) -> str:
        """Get a key identifying the client (IP-based)."""
        # Use X-Forwarded-For on Replit (behind proxy)
        forwarded = request.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.remote_addr or 'unknown'

    def _cleanup_old_entries(self):
        """Periodically remove expired entries to prevent memory growth."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        self._last_cleanup = now
        cutoff = now - 300  # Remove entries older than 5 minutes
        keys_to_remove = []

        for key, timestamps in self._hits.items():
            self._hits[key] = [t for t in timestamps if t > cutoff]
            if not self._hits[key]:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._hits[key]

    def is_rate_limited(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Check if a key has exceeded its rate limit.

        Returns True if rate limited, False if allowed.
        """
        now = time.time()
        cutoff = now - window_seconds

        with self._lock:
            self._cleanup_old_entries()

            # Remove old timestamps outside the window
            self._hits[key] = [t for t in self._hits[key] if t > cutoff]

            if len(self._hits[key]) >= max_requests:
                return True

            # Record this request
            self._hits[key].append(now)
            return False

    def _get_retry_after(self, key: str, window_seconds: int) -> int:
        """Calculate seconds until the oldest entry in the window expires."""
        timestamps = self._hits.get(key, [])
        if not timestamps:
            return 1
        oldest = min(timestamps)
        retry_after = math.ceil((oldest + window_seconds) - time.time())
        return max(retry_after, 1)

    def _check_rate_limit(self):
        """Flask before_request hook to enforce rate limits."""
        path = request.path

        # Exempt static assets and health checks
        for prefix in self._exempt_prefixes:
            if path.startswith(prefix):
                return None

        # Non-API routes: no rate limit
        if not path.startswith('/api/') and path not in ('/login',):
            # Check if there's a specific route limit set (e.g. /login)
            has_specific = False
            for prefix in self._route_limits:
                if path.startswith(prefix):
                    has_specific = True
                    break
            if not has_specific:
                return None

        # Determine the appropriate limit
        max_requests, window = self.default_api_limit

        # Check route-specific overrides (most specific prefix wins)
        matched_prefix = ''
        for prefix, limit in self._route_limits.items():
            if path.startswith(prefix) and len(prefix) > len(matched_prefix):
                matched_prefix = prefix
                max_requests, window = limit

        # Build rate limit key: IP + matched prefix (or path group)
        client_key = self._get_client_key()
        if matched_prefix:
            rate_key = f"{client_key}:{matched_prefix}"
        else:
            path_group = path.split('/')[1] if '/' in path[1:] else path
            rate_key = f"{client_key}:{path_group}"

        if self.is_rate_limited(rate_key, max_requests, window):
            retry_after = self._get_retry_after(rate_key, window)
            logging.warning(f"[RATE LIMIT] {client_key} exceeded {max_requests}/{window}s on {path}")
            response = jsonify({
                'status': 'error',
                'message': 'Rate limit exceeded. Please slow down.',
            })
            response.status_code = 429
            response.headers['Retry-After'] = str(retry_after)
            return response

        return None


# Global singleton
limiter = RateLimiter()


def rate_limit(limit_str: str):
    """Decorator to apply a specific rate limit to a route.

    Usage:
        @rate_limit('10/minute')
        @rate_limit('5/second')
        @rate_limit('100/hour')
    """
    # Parse limit string
    parts = limit_str.split('/')
    max_requests = int(parts[0])
    period = parts[1].lower()
    window_map = {'second': 1, 'minute': 60, 'hour': 3600, 'day': 86400}
    window_seconds = window_map.get(period, 60)

    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            client_key = limiter._get_client_key()
            rate_key = f"{client_key}:{f.__name__}"

            if limiter.is_rate_limited(rate_key, max_requests, window_seconds):
                retry_after = limiter._get_retry_after(rate_key, window_seconds)
                logging.warning(f"[RATE LIMIT] {client_key} exceeded {limit_str} on {f.__name__}")
                response = jsonify({
                    'status': 'error',
                    'message': 'Rate limit exceeded. Please slow down.',
                })
                response.status_code = 429
                response.headers['Retry-After'] = str(retry_after)
                return response

            return f(*args, **kwargs)
        return decorated
    return decorator
