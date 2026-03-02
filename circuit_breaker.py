"""
Circuit Breaker pattern for external service calls.

Tracks consecutive failures per service (Apollo, GitHub API, AI) and trips
after a configurable threshold. When tripped, calls are rejected immediately
(fail-fast) until a cooldown period expires. On cooldown expiry the breaker
moves to half-open: the next call is allowed through as a probe; if it
succeeds the breaker resets, otherwise it trips again.

Usage as a decorator:

    @circuit_breaker('apollo')
    def call_apollo_api(...):
        ...

Or as a context manager:

    with CircuitBreaker.get('github') as cb:
        response = requests.get(...)
        cb.record_success()
"""
import functools
import logging
import threading
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = 'closed'        # Normal operation — calls pass through
    OPEN = 'open'            # Tripped — calls are rejected immediately
    HALF_OPEN = 'half_open'  # Probe phase — one call allowed to test recovery


class CircuitBreakerError(Exception):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, service: str, retry_after: float):
        self.service = service
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker OPEN for '{service}'. "
            f"Retry after {retry_after:.0f}s."
        )


class CircuitBreaker:
    """Per-service circuit breaker with thread-safe state management."""

    # Global registry of all breaker instances
    _registry: Dict[str, 'CircuitBreaker'] = {}
    _registry_lock = threading.Lock()

    # Default thresholds
    DEFAULT_FAILURE_THRESHOLD = 5
    DEFAULT_COOLDOWN_SECONDS = 900  # 15 minutes

    def __init__(
        self,
        service: str,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        on_trip: Optional[Callable[[str], None]] = None,
        on_reset: Optional[Callable[[str], None]] = None,
    ):
        self.service = service
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._on_trip = on_trip
        self._on_reset = on_reset

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time: Optional[float] = None
        self._tripped_at: Optional[float] = None
        self._total_trips = 0
        self._total_successes = 0
        self._total_failures = 0

    # -----------------------------------------------------------------
    # Registry
    # -----------------------------------------------------------------

    @classmethod
    def get(cls, service: str, **kwargs) -> 'CircuitBreaker':
        """Get or create a CircuitBreaker for the given service name."""
        with cls._registry_lock:
            if service not in cls._registry:
                cls._registry[service] = cls(service, **kwargs)
            return cls._registry[service]

    @classmethod
    def get_all(cls) -> Dict[str, 'CircuitBreaker']:
        """Return a snapshot of all registered breakers."""
        with cls._registry_lock:
            return dict(cls._registry)

    # -----------------------------------------------------------------
    # State queries
    # -----------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._evaluate_state()

    def _evaluate_state(self) -> CircuitState:
        """Must be called while holding self._lock."""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - (self._tripped_at or 0)
            if elapsed >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                logger.info(
                    f"[CIRCUIT-BREAKER] '{self.service}' moved to HALF_OPEN "
                    f"after {elapsed:.0f}s cooldown"
                )
        return self._state

    def is_available(self) -> bool:
        """True if calls can pass through (CLOSED or HALF_OPEN)."""
        return self.state != CircuitState.OPEN

    def status(self) -> dict:
        """Return a JSON-serialisable status snapshot."""
        with self._lock:
            state = self._evaluate_state()
            retry_after = 0.0
            if state == CircuitState.OPEN and self._tripped_at:
                remaining = self.cooldown_seconds - (time.time() - self._tripped_at)
                retry_after = max(remaining, 0)
            return {
                'service': self.service,
                'state': state.value,
                'consecutive_failures': self._consecutive_failures,
                'failure_threshold': self.failure_threshold,
                'cooldown_seconds': self.cooldown_seconds,
                'retry_after_seconds': round(retry_after, 1),
                'tripped_at': (
                    datetime.fromtimestamp(self._tripped_at).isoformat()
                    if self._tripped_at else None
                ),
                'total_trips': self._total_trips,
                'total_successes': self._total_successes,
                'total_failures': self._total_failures,
            }

    # -----------------------------------------------------------------
    # Recording outcomes
    # -----------------------------------------------------------------

    def record_success(self):
        """Record a successful call — resets the breaker to CLOSED."""
        with self._lock:
            self._total_successes += 1
            if self._state in (CircuitState.HALF_OPEN, CircuitState.CLOSED):
                if self._consecutive_failures > 0:
                    logger.info(
                        f"[CIRCUIT-BREAKER] '{self.service}' success — "
                        f"resetting from {self._consecutive_failures} consecutive failures"
                    )
                self._consecutive_failures = 0
                was_half_open = self._state == CircuitState.HALF_OPEN
                self._state = CircuitState.CLOSED
                self._tripped_at = None
                if was_half_open and self._on_reset:
                    try:
                        self._on_reset(self.service)
                    except Exception:
                        pass

    def record_failure(self, error: Optional[Exception] = None):
        """Record a failed call — may trip the breaker."""
        with self._lock:
            self._consecutive_failures += 1
            self._total_failures += 1
            self._last_failure_time = time.time()

            logger.warning(
                f"[CIRCUIT-BREAKER] '{self.service}' failure "
                f"({self._consecutive_failures}/{self.failure_threshold})"
                + (f": {error}" if error else "")
            )

            if self._consecutive_failures >= self.failure_threshold:
                self._trip()

    def _trip(self):
        """Trip the breaker to OPEN. Must be called while holding self._lock."""
        self._state = CircuitState.OPEN
        self._tripped_at = time.time()
        self._total_trips += 1
        logger.error(
            f"[CIRCUIT-BREAKER] '{self.service}' TRIPPED — "
            f"{self._consecutive_failures} consecutive failures. "
            f"Blocking calls for {self.cooldown_seconds}s."
        )
        if self._on_trip:
            try:
                self._on_trip(self.service)
            except Exception:
                pass

    def force_reset(self):
        """Manually reset the breaker (e.g. after fixing an outage)."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._tripped_at = None
            logger.info(f"[CIRCUIT-BREAKER] '{self.service}' manually reset")

    def force_trip(self):
        """Manually trip the breaker (e.g. maintenance window)."""
        with self._lock:
            self._trip()

    # -----------------------------------------------------------------
    # Guard: raise if circuit is open
    # -----------------------------------------------------------------

    def guard(self):
        """Raise CircuitBreakerError if the circuit is open."""
        with self._lock:
            state = self._evaluate_state()
            if state == CircuitState.OPEN:
                remaining = self.cooldown_seconds - (time.time() - (self._tripped_at or 0))
                raise CircuitBreakerError(self.service, max(remaining, 0))

    # -----------------------------------------------------------------
    # Context manager protocol
    # -----------------------------------------------------------------

    def __enter__(self):
        self.guard()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.record_success()
        else:
            self.record_failure(exc_val)
        return False  # Do not suppress exceptions


# =====================================================================
# Decorator
# =====================================================================

def circuit_breaker(
    service: str,
    failure_threshold: int = CircuitBreaker.DEFAULT_FAILURE_THRESHOLD,
    cooldown_seconds: int = CircuitBreaker.DEFAULT_COOLDOWN_SECONDS,
):
    """
    Decorator that wraps a function with circuit-breaker protection.

    Example:
        @circuit_breaker('apollo')
        def search_apollo_contacts(domain):
            ...

    When the breaker is open, CircuitBreakerError is raised immediately
    without calling the wrapped function.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cb = CircuitBreaker.get(
                service,
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
            cb.guard()
            try:
                result = func(*args, **kwargs)
                cb.record_success()
                return result
            except CircuitBreakerError:
                raise
            except Exception as e:
                cb.record_failure(e)
                raise
        return wrapper
    return decorator
