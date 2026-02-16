"""
Utility functions for 3-Signal Internationalization Intent Scanner.

Provides helper functions for signal detection and analysis.
"""
import re
import time
import random
import threading
import requests
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from config import Config

# Import caching layer
from cache import get_github_cache, CacheKeyBuilder

# Import database functions for hourly API tracking
from database import increment_hourly_api_calls


@dataclass
class TokenStatus:
    """Status tracking for a single GitHub token."""
    token: str
    remaining: int = 5000  # GitHub's default limit
    limit: int = 5000
    reset_time: int = 0  # Unix timestamp when limit resets
    last_used: float = 0.0
    request_count: int = 0
    is_rate_limited: bool = False

    @property
    def masked_token(self) -> str:
        """Return masked version of token for logging (first 4 + last 4 chars)."""
        if len(self.token) > 8:
            return f"{self.token[:4]}...{self.token[-4:]}"
        return "****"

    @property
    def usage_percent(self) -> float:
        """Return percentage of rate limit used."""
        if self.limit == 0:
            return 100.0
        return ((self.limit - self.remaining) / self.limit) * 100


class TokenPool:
    """
    Intelligent token pool manager with per-token rate limit tracking.

    Features:
    - Tracks remaining rate limit for each token
    - Selects token with highest remaining capacity
    - Automatically skips rate-limited tokens
    - Thread-safe for concurrent usage
    - Provides visibility into pool health

    BDR Benefit: With 10 BDRs contributing tokens, you get 50,000 requests/hour
    instead of 5,000. That's 250+ company scans continuously without pausing.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens: Dict[str, TokenStatus] = {}
        self._load_tokens()

    def _load_tokens(self):
        """Load tokens from Config and initialize status tracking."""
        tokens = Config.GITHUB_TOKENS or []
        for token in tokens:
            if token and token not in self._tokens:
                self._tokens[token] = TokenStatus(token=token)

    def reload_tokens(self):
        """Reload tokens from Config (useful if tokens change at runtime)."""
        with self._lock:
            # Preserve existing token stats, add new tokens
            new_tokens = Config.get_github_tokens()
            for token in new_tokens:
                if token and token not in self._tokens:
                    self._tokens[token] = TokenStatus(token=token)

    def get_best_token(self) -> Optional[str]:
        """
        Get the token with the highest remaining rate limit.

        Selection strategy:
        1. Skip tokens that are currently rate-limited (remaining=0 or is_rate_limited)
        2. Among available tokens, select the one with highest 'remaining' count
        3. If all tokens are rate-limited, return the one that resets soonest

        Thread-safe: uses a lock to ensure consistent selection.

        Returns:
            Best available token string, or None if no tokens configured.
        """
        if not self._tokens:
            return None

        with self._lock:
            now = time.time()
            available = []
            rate_limited = []

            for token, status in self._tokens.items():
                # Check if token has recovered from rate limit
                if status.is_rate_limited and now >= status.reset_time:
                    status.is_rate_limited = False
                    status.remaining = status.limit  # Assume reset

                if status.is_rate_limited or status.remaining <= 0:
                    rate_limited.append((token, status))
                else:
                    available.append((token, status))

            if available:
                # Sort by remaining (highest first), then by last_used (oldest first)
                available.sort(key=lambda x: (-x[1].remaining, x[1].last_used))
                best_token, best_status = available[0]
                best_status.last_used = now
                best_status.request_count += 1
                return best_token

            # All tokens rate-limited - return the one that resets soonest
            if rate_limited:
                rate_limited.sort(key=lambda x: x[1].reset_time)
                return rate_limited[0][0]

            return None

    def update_token_status(self, token: str, remaining: int, limit: int, reset_time: int):
        """
        Update rate limit status for a token after an API response.

        Args:
            token: The token that was used
            remaining: X-RateLimit-Remaining header value
            limit: X-RateLimit-Limit header value
            reset_time: X-RateLimit-Reset header value (Unix timestamp)
        """
        if token not in self._tokens:
            return

        with self._lock:
            status = self._tokens[token]
            status.remaining = remaining
            status.limit = limit
            status.reset_time = reset_time
            status.is_rate_limited = (remaining <= 0)

    def mark_rate_limited(self, token: str, reset_time: int):
        """Mark a token as rate-limited (e.g., after receiving 429)."""
        if token not in self._tokens:
            return

        with self._lock:
            status = self._tokens[token]
            status.remaining = 0
            status.reset_time = reset_time
            status.is_rate_limited = True

    def get_pool_status(self) -> Dict[str, Any]:
        """
        Get current status of all tokens in the pool.

        Returns:
            Dict with pool statistics and per-token status.
        """
        with self._lock:
            now = time.time()
            total_remaining = 0
            total_limit = 0
            available_count = 0
            rate_limited_count = 0
            token_statuses = []

            for token, status in self._tokens.items():
                total_remaining += status.remaining
                total_limit += status.limit

                if status.is_rate_limited or status.remaining <= 0:
                    rate_limited_count += 1
                    time_until_reset = max(0, status.reset_time - now)
                else:
                    available_count += 1
                    time_until_reset = 0

                token_statuses.append({
                    'token': status.masked_token,
                    'remaining': status.remaining,
                    'limit': status.limit,
                    'usage_percent': round(status.usage_percent, 1),
                    'request_count': status.request_count,
                    'is_rate_limited': status.is_rate_limited,
                    'resets_in_seconds': int(time_until_reset),
                })

            return {
                'pool_size': len(self._tokens),
                'tokens_available': available_count,
                'tokens_rate_limited': rate_limited_count,
                'total_remaining': total_remaining,
                'total_limit': total_limit,
                'effective_hourly_capacity': total_limit,
                'token_details': token_statuses,
            }

    def get_token_count(self) -> int:
        """Return the number of tokens in the pool."""
        return len(self._tokens)

    def has_available_tokens(self) -> bool:
        """Check if any tokens are available (not rate-limited)."""
        with self._lock:
            now = time.time()
            for status in self._tokens.values():
                if status.is_rate_limited and now >= status.reset_time:
                    return True
                if not status.is_rate_limited and status.remaining > 0:
                    return True
            return False


# Global token pool instance
_token_pool = TokenPool()

# Reusable HTTP session for connection pooling (keep-alive)
_session = requests.Session()


def get_token_pool() -> TokenPool:
    """Get the global token pool instance."""
    return _token_pool


def get_token_pool_status() -> Dict[str, Any]:
    """Get current status of the token pool (convenience function)."""
    return _token_pool.get_pool_status()


def get_github_headers(token: Optional[str] = None) -> dict:
    """
    Get headers for GitHub API requests with intelligent token selection.

    If a token is provided, uses that token. Otherwise, selects the best
    available token from the pool (highest remaining rate limit).

    Falls back to Config.GITHUB_TOKEN if no tokens in pool.

    Thread-safe: can be called from multiple threads simultaneously.

    Args:
        token: Optional specific token to use (bypasses pool selection)

    Returns:
        Dict of headers including Authorization if token available.
    """
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'Lead-Machine/1.0'
    }

    # Use provided token, or get best from pool
    if token is None:
        token = _token_pool.get_best_token()

    if token:
        headers['Authorization'] = f'token {token}'
    elif Config.GITHUB_TOKEN:
        # Fallback to single token if pool is empty
        headers['Authorization'] = f'token {Config.GITHUB_TOKEN}'

    return headers


def is_bot_account(username: str) -> bool:
    """
    Check if a username is a known bot account.

    Args:
        username: GitHub username to check

    Returns:
        True if the username matches a known bot pattern.
    """
    if not username:
        return False

    username_lower = username.lower()

    # Check against known bot accounts
    if username_lower in [b.lower() for b in Config.BOT_ACCOUNTS]:
        return True

    # Check for common bot patterns
    bot_patterns = ['[bot]', '-bot', '_bot', 'bot-', 'bot_', 'automation']
    return any(pattern in username_lower for pattern in bot_patterns)


def get_framework_from_libraries(libraries: list) -> Optional[str]:
    """
    Get the primary framework from detected i18n libraries.

    Args:
        libraries: List of detected i18n library names

    Returns:
        Primary framework name (e.g., 'Next.js', 'React') or None.
    """
    if not libraries:
        return None

    # Priority order for frameworks (more specific first)
    priority = ['Next.js', 'React', 'Vue', 'Angular', 'Django', 'Laravel', 'Ruby', 'Elixir', 'Python']

    detected_frameworks = set()
    for lib in libraries:
        framework = Config.I18N_LIBRARIES.get(lib)
        if framework:
            detected_frameworks.add(framework)

    for pf in priority:
        if pf in detected_frameworks:
            return pf

    return list(detected_frameworks)[0] if detected_frameworks else None


def format_signal_for_output(signal: dict) -> dict:
    """
    Format a signal object for the standardized output format.

    Output Format:
    {
        "Company": "Name",
        "Signal": "Dependency Injection",
        "Evidence": "Found react-intl in package.json but no locales folder",
        "Link": "URL_TO_FILE"
    }

    Args:
        signal: Raw signal dict from scanner

    Returns:
        Formatted signal dict
    """
    return {
        'Company': signal.get('Company', 'Unknown'),
        'Signal': signal.get('Signal', signal.get('type', 'Unknown')),
        'Evidence': signal.get('Evidence', ''),
        'Link': signal.get('Link', signal.get('url', '')),
    }


def summarize_signals(signals: list) -> dict:
    """
    Create a summary of detected signals.

    Args:
        signals: List of signal objects

    Returns:
        Summary dict with counts and categorized signals
    """
    summary = {
        'total': len(signals),
        'by_type': {
            'rfc_discussion': [],
            'dependency_injection': [],
            'ghost_branch': [],
        },
        'high_priority_count': 0,
    }

    for signal in signals:
        signal_type = signal.get('type', 'unknown')

        if signal_type in summary['by_type']:
            summary['by_type'][signal_type].append(signal)

        if signal.get('priority') == 'HIGH':
            summary['high_priority_count'] += 1

    return summary


def get_phase_from_signal_type(signal_type: str) -> str:
    """
    Map signal type to internationalization phase.

    Args:
        signal_type: Type of signal (rfc_discussion, dependency_injection, ghost_branch)

    Returns:
        Phase name (Thinking, Preparing, Active)
    """
    phase_mapping = {
        'rfc_discussion': 'Thinking',
        'dependency_injection': 'Preparing',
        'ghost_branch': 'Active',
    }
    return phase_mapping.get(signal_type, 'Unknown')


class CachedResponse:
    """
    A fake Response object that mimics requests.Response for cached data.

    This allows cached responses to be used transparently in code that
    expects a requests.Response object.
    """

    def __init__(self, status_code: int, headers: Dict, body: Any):
        self.status_code = status_code
        self._headers = headers
        self._body = body
        self._json_data = body
        self.from_cache = True
        self.reason = "OK" if status_code == 200 else "Cached"

    @property
    def headers(self):
        return self._headers

    def json(self):
        return self._json_data

    @property
    def text(self):
        import json
        return json.dumps(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"Cached response with status {self.status_code}")


def make_github_request(url: str, params: Optional[dict] = None, timeout: int = 30, priority: str = 'normal', _retry_count: int = 0, skip_cache: bool = False) -> requests.Response:
    """
    Enhanced GitHub API request wrapper with intelligent token pool management and caching.

    Features:
    - Redis/DiskCache: Caches responses with endpoint-aware TTLs
    - Token Pool: Automatically selects the token with highest remaining rate limit
    - Per-Token Tracking: Updates rate limit status after each request
    - Smart Switching: On 429, immediately switches to another token if available
    - Buffering: Starts slowing down when remaining limit < 50
    - Jitter: Random delay to prevent concurrent workers from hitting limit at once
    - Priority aware: Can prioritize 'high' priority requests (Discovery)

    Cache TTLs (configurable via environment):
    - Organization metadata: 24 hours
    - Repository lists: 7 days
    - File contents: 7 days
    - Branch/PR lists: 12 hours
    - Issue lists: 6 hours

    With caching enabled, re-scans use 60% fewer API calls.
    With 10 BDRs contributing tokens (50,000 req/hr), you can scan 250+ companies
    continuously without pausing.

    Args:
        url: GitHub API URL
        params: Query parameters
        timeout: Request timeout in seconds
        priority: 'high' or 'normal' - affects buffering behavior
        _retry_count: Internal retry counter
        skip_cache: If True, bypass cache and force fresh request

    Returns:
        requests.Response object (or CachedResponse for cached data)
    """
    MAX_RETRIES = 3
    cache = get_github_cache()

    # 1. Check cache first (unless skip_cache is True)
    if not skip_cache and cache.is_available():
        cached = cache.get(url, params)
        if cached:
            status_code, headers, body = cached
            # Return a fake response object that mimics requests.Response
            return CachedResponse(status_code, headers, body)

    # 2. Add small random jitter to help de-sync concurrent threads
    time.sleep(random.uniform(0.01, 0.1))

    # 3. Get the best available token from the pool
    token = _token_pool.get_best_token()

    # 4. Make the request (using session for connection pooling)
    try:
        response = _session.get(
            url,
            headers=get_github_headers(token),
            params=params,
            timeout=timeout or 30,
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError) as e:
        if _retry_count < MAX_RETRIES:
            backoff = min(2 ** _retry_count, 30) + random.uniform(0, 1)
            print(f"[GITHUB] Connection error, retrying in {backoff:.1f}s (attempt {_retry_count+1}/{MAX_RETRIES})")
            time.sleep(backoff)
            return make_github_request(url, params, timeout, priority, _retry_count + 1, skip_cache)
        raise

    # 5. Extract rate limit info from response headers
    remaining_header = response.headers.get("X-RateLimit-Remaining")
    limit_header = response.headers.get("X-RateLimit-Limit")
    reset_header = response.headers.get("X-RateLimit-Reset")

    remaining = None
    limit = 5000
    reset_time = 0

    try:
        if remaining_header:
            remaining = int(remaining_header)
        if limit_header:
            limit = int(limit_header)
        if reset_header:
            reset_time = int(reset_header)
    except ValueError:
        pass

    # 6. Update token status in the pool
    if token and remaining is not None:
        _token_pool.update_token_status(token, remaining, limit, reset_time)

    # 7. Handle rate limiting with smart token switching
    if response.status_code == 429:
        # Mark current token as rate-limited
        if token:
            _token_pool.mark_rate_limited(token, reset_time)

        # Check if other tokens are available
        pool_status = _token_pool.get_pool_status()

        if pool_status['tokens_available'] > 0 and _retry_count < MAX_RETRIES:
            # Another token is available - retry immediately with different token
            print(f"[TOKEN_POOL] Token exhausted, switching to another ({pool_status['tokens_available']} available)")
            return make_github_request(url, params, timeout, priority, _retry_count + 1, skip_cache)
        else:
            # All tokens exhausted - must wait
            sleep_for = max(reset_time - int(time.time()), 0) + 1
            print(f"[TOKEN_POOL] All {pool_status['pool_size']} tokens exhausted! Waiting {sleep_for}s for reset...")
            time.sleep(sleep_for)

            if _retry_count < MAX_RETRIES:
                return make_github_request(url, params, timeout, priority, _retry_count + 1, skip_cache)

        return response

    # 7b. Handle 5xx server errors with exponential backoff
    if response.status_code in (500, 502, 503, 504) and _retry_count < MAX_RETRIES:
        backoff = min(2 ** _retry_count, 30) + random.uniform(0, 1)
        print(f"[GITHUB] {response.status_code} error, retrying in {backoff:.1f}s (attempt {_retry_count+1}/{MAX_RETRIES})")
        time.sleep(backoff)
        return make_github_request(url, params, timeout, priority, _retry_count + 1, skip_cache)

    # 8. Cache successful responses and track API calls
    if response.status_code == 200:
        # Only count successful API calls (not 429s, 404s, etc.)
        try:
            increment_hourly_api_calls(1)
        except Exception:
            pass  # Don't let stats tracking break requests

        if cache.is_available():
            try:
                body = response.json()
                cache.set(url, params, response.status_code, dict(response.headers), body)
            except Exception:
                # Don't fail if caching fails - just continue
                pass

    # 9. Soft buffering when approaching limit (only if we're running low)
    if remaining is not None and remaining < 10:
        # Check if other tokens have capacity - if so, skip buffering
        pool_status = _token_pool.get_pool_status()
        other_tokens_have_capacity = pool_status['total_remaining'] > remaining + 100

        if not other_tokens_have_capacity:
            # Only buffer if we are critically low (< 3 requests)
            if remaining < 3:
                sleep_for = 2.0
                print(f"[TOKEN_POOL] Token critical ({remaining} remaining), buffering {sleep_for:.1f}s...")
                time.sleep(sleep_for)
            elif remaining < 5 and priority != 'high':
                # Slight pause for low priority requests when running on fumes
                time.sleep(0.5)

    # Mark response as not from cache
    response.from_cache = False
    return response
