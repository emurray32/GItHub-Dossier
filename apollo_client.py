"""
Apollo Client — Lightweight API wrapper for Apollo.io.

Provides:
    - ApolloRateLimiter: Thread-safe token-bucket rate limiter (50 req/min)
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
    """Thread-safe token-bucket rate limiter for Apollo API (50 req/min)."""

    def __init__(self, max_tokens=50, refill_period=60.0):
        self._max_tokens = max_tokens
        self._refill_period = refill_period
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        added = elapsed * (self._max_tokens / self._refill_period)
        self._tokens = min(self._max_tokens, self._tokens + added)
        self._last_refill = now

    def acquire(self, timeout=120.0):
        """Block until a token is available. Returns True, or False on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    @property
    def available_tokens(self):
        with self._lock:
            self._refill()
            return int(self._tokens)


rate_limiter = ApolloRateLimiter(max_tokens=50, refill_period=60.0)


def apollo_api_call(method, url, **kwargs):
    """Rate-limited Apollo API call.

    Args:
        method: 'get' or 'post'
        url: Apollo API endpoint
        **kwargs: passed to requests.get/post

    Returns:
        requests.Response object

    Raises:
        RuntimeError if rate limit timeout exceeded or API key missing.
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

    if not rate_limiter.acquire(timeout=120):
        raise RuntimeError('Apollo rate limit timeout — too many requests queued')

    if method.lower() == 'get':
        return req.get(url, **kwargs)
    return req.post(url, **kwargs)


def resolve_email_account():
    """Resolve the Apollo sending email account ID."""
    preferred_sender = os.environ.get('APOLLO_SENDER_EMAIL', '').strip().lower()
    try:
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
    except Exception as e:
        logger.warning(f"Could not fetch Apollo email accounts: {e}")
    return None


def resolve_custom_field_ids():
    """Fetch Apollo custom field ID mapping."""
    field_id_map = {}
    try:
        cf_resp = apollo_api_call('get', 'https://api.apollo.io/v1/typed_custom_fields')
        if cf_resp.status_code == 200:
            for f in cf_resp.json().get('typed_custom_fields', []):
                fid = f.get('id')
                name = (f.get('name') or '').lower().replace(' ', '_')
                if fid and name:
                    field_id_map[name] = fid
    except Exception as e:
        logger.warning(f"Could not fetch Apollo custom fields: {e}")
    return field_id_map
