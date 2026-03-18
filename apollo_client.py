"""
Apollo Client — Lightweight API wrapper for Apollo.io.

Provides:
    - ApolloRateLimiter: Thread-safe fixed-window rate limiter (50 req/60s)
    - apollo_api_call(): Rate-limited wrapper for all Apollo API requests
    - resolve_email_account(): Find the active sending email account
    - resolve_custom_field_ids(): Fetch custom field ID mapping
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)


class ApolloRateLimiter:
    """Thread-safe fixed-window rate limiter for Apollo API (50 req/60s)."""

    def __init__(self, max_requests=50, window_seconds=60.0):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._request_count = 0
        self._window_start = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout=120.0):
        """Block until a request slot is available. Returns True, or False on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                # Reset window if it has elapsed
                if now >= self._window_start + self._window_seconds:
                    self._request_count = 0
                    self._window_start = now
                if self._request_count < self._max_requests:
                    self._request_count += 1
                    return True
                # Calculate sleep time until window resets
                sleep_time = (self._window_start + self._window_seconds) - now
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(sleep_time, 0.5))

    @property
    def available_requests(self):
        with self._lock:
            now = time.monotonic()
            if now >= self._window_start + self._window_seconds:
                return self._max_requests
            return max(0, self._max_requests - self._request_count)


rate_limiter = ApolloRateLimiter(max_requests=50, window_seconds=60.0)


def apollo_api_call(method, url, **kwargs):
    """Rate-limited Apollo API call.

    Args:
        method: 'get', 'post', 'put', 'patch', or 'delete'
        url: Apollo API endpoint
        **kwargs: passed to the underlying requests method

    Returns:
        requests.Response object

    Raises:
        RuntimeError if rate limit timeout exceeded or API key missing.
        ValueError if an unsupported HTTP method is provided.
    """
    import requests as req

    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        raise RuntimeError('Apollo API key not configured (APOLLO_API_KEY)')

    headers = kwargs.pop('headers', {})
    headers.setdefault('X-Api-Key', apollo_key)
    headers.setdefault('Content-Type', 'application/json')
    kwargs['headers'] = headers
    kwargs.setdefault('timeout', 15)

    dispatch = {
        'get': req.get,
        'post': req.post,
        'put': req.put,
        'patch': req.patch,
        'delete': req.delete,
    }
    method_lower = method.lower()
    if method_lower not in dispatch:
        raise ValueError(f"Unsupported HTTP method: {method!r}")

    if not rate_limiter.acquire(timeout=120):
        raise RuntimeError('Apollo rate limit timeout — too many requests queued')

    resp = dispatch[method_lower](url, **kwargs)

    # Handle 429 rate-limit response — retry once after Retry-After delay
    if resp.status_code == 429:
        retry_after = int(resp.headers.get('Retry-After', 10))
        logger.warning(f"Apollo 429 rate-limited; retrying after {retry_after}s")
        time.sleep(retry_after)
        if not rate_limiter.acquire(timeout=120):
            raise RuntimeError('Apollo rate limit timeout — too many requests queued')
        resp = dispatch[method_lower](url, **kwargs)

    return resp


def resolve_email_account():
    """Resolve the Apollo sending email account ID.

    Raises on failure — the caller is responsible for retry/fallback logic.
    """
    preferred_sender = os.environ.get('APOLLO_SENDER_EMAIL', '').strip().lower()
    ea_resp = apollo_api_call('get', 'https://api.apollo.io/api/v1/email_accounts')
    if ea_resp.status_code == 200:
        accounts = ea_resp.json().get('email_accounts', [])
        active = [a for a in accounts if a.get('active')]
        if preferred_sender:
            match = next(
                (a for a in active if a.get('email', '').lower() == preferred_sender),
                None
            )
            return match['id'] if match else (active[0]['id'] if active else None)
        elif active:
            return active[0]['id']
    logger.error(f"Apollo email_accounts request failed with status {ea_resp.status_code}")
    raise RuntimeError(f"Failed to resolve Apollo email account (HTTP {ea_resp.status_code})")


def resolve_custom_field_ids():
    """Fetch Apollo custom field ID mapping.

    Raises on failure — the caller is responsible for retry/fallback logic.
    """
    field_id_map = {}
    cf_resp = apollo_api_call('get', 'https://api.apollo.io/api/v1/custom_fields')
    if cf_resp.status_code == 200:
        for f in cf_resp.json().get('custom_fields', []):
            fid = f.get('id')
            name = (f.get('name') or '').lower().replace(' ', '_')
            if fid and name:
                field_id_map[name] = fid
        return field_id_map
    logger.error(f"Apollo custom_fields request failed with status {cf_resp.status_code}")
    raise RuntimeError(f"Failed to resolve Apollo custom field IDs (HTTP {cf_resp.status_code})")
