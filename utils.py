"""
Utility functions for 3-Signal Internationalization Intent Scanner.

Provides helper functions for signal detection and analysis.
"""
import re
import time
import random
import threading
import requests
from itertools import cycle
from typing import Optional, Dict, Any, List
from config import Config


# Thread-safe token rotation using round-robin strategy
class TokenRotator:
    """Thread-safe token rotator using round-robin selection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens = Config.GITHUB_TOKENS or []
        self._cycle = cycle(self._tokens) if self._tokens else None

    def get_token(self) -> Optional[str]:
        """
        Get the next token in round-robin order.

        Thread-safe: uses a lock to ensure consistent rotation
        across multiple threads.

        Returns:
            Next token string, or None if no tokens configured.
        """
        if not self._cycle:
            return None

        with self._lock:
            return next(self._cycle)

    def reload_tokens(self):
        """Reload tokens from Config (useful if tokens change at runtime)."""
        with self._lock:
            self._tokens = Config.GITHUB_TOKENS or []
            self._cycle = cycle(self._tokens) if self._tokens else None


# Global token rotator instance
_token_rotator = TokenRotator()


def get_github_headers() -> dict:
    """
    Get headers for GitHub API requests with token rotation.

    Uses round-robin selection from GITHUB_TOKENS if available,
    otherwise falls back to the single GITHUB_TOKEN for backward
    compatibility.

    Thread-safe: can be called from multiple threads simultaneously.
    """
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'Lead-Machine/1.0'
    }

    # Try to get a token from the rotator (uses GITHUB_TOKENS if available)
    token = _token_rotator.get_token()

    if token:
        headers['Authorization'] = f'token {token}'
    elif Config.GITHUB_TOKEN:
        # Fallback to single token if rotator has no tokens
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


def make_github_request(url: str, params: Optional[dict] = None, timeout: int = 30, priority: str = 'normal') -> requests.Response:
    """
    Enhanced GitHub API request wrapper with intelligent rate-limit buffering.
    
    Features:
    - Buffering: Starts slowing down when remaining limit < 50
    - Pre-emptive sleep: Sleeps longer as limit approaches zero
    - Jitter: Random delay to prevent concurrent workers from hitting limit at once
    - Priority aware: Can prioritize 'high' priority requests (Discovery)
    """
    # 1. Add small random jitter to help de-sync concurrent threads
    time.sleep(random.uniform(0.01, 0.1))

    response = requests.get(
        url,
        headers=get_github_headers(),
        params=params,
        timeout=timeout,
    )

    remaining_header = response.headers.get("X-RateLimit-Remaining")
    if remaining_header is not None:
        try:
            remaining = int(remaining_header)
        except ValueError:
            remaining = None

        if remaining is not None:
            # SOFT BUFFERING: Start slowing down early
            if remaining < 50:
                reset_header = response.headers.get("X-RateLimit-Reset", "0")
                try:
                    reset_time = int(reset_header)
                    now = int(time.time())
                    wait_seconds = max(reset_time - now, 1)
                except ValueError:
                    wait_seconds = 10

                # Calculate staggered sleep
                # If remaining is 1, sleep almost the whole way to reset
                # If remaining is 49, sleep just a tiny bit
                if remaining < 10:
                    # Critical zone: Sleep 50% of the reset time or at least 10s
                    # but if priority is high (Discovery), sleep less
                    sleep_factor = 0.5 if priority != 'high' else 0.2
                    sleep_for = max(wait_seconds * sleep_factor, 10)
                else:
                    # Warning zone: Small variable sleep
                    sleep_for = random.uniform(1.0, 3.0)

                if remaining < 5 or (remaining < 20 and priority != 'high'):
                    print(f"[RATE_LIMIT] Remaining: {remaining}. priority: {priority}. Buffering for {sleep_for:.1f}s...")
                    time.sleep(sleep_for)

    # Handle 429 explicitly if hit
    if response.status_code == 429:
        reset_header = response.headers.get("X-RateLimit-Reset", "0")
        try:
            reset_time = int(reset_header)
            sleep_for = max(reset_time - int(time.time()), 0) + 1
        except ValueError:
            sleep_for = 60
        
        print(f"[RATE_LIMIT] Hit 429! Sleeping for {sleep_for}s...")
        time.sleep(sleep_for)
        # Retry once
        return make_github_request(url, params, timeout, priority)

    return response
